from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from langchain_core.messages import HumanMessage
from sqlmodel import Session

from applysync.config import SourcesConfig
from applysync.db import repository as repo
from applysync.gmail.query_builder import guess_platform
from applysync.pipeline.state import ClassifyAndExtractResult, EmailState, JobApplicationEvent, MatchDecision

logger = logging.getLogger(__name__)

# Canonical stand-in when an email genuinely doesn't mention a job title
# (some ATS confirmations don't repeat it). Using one fixed string, not
# whatever placeholder text the model might otherwise invent, is what lets
# find_matching_application dedupe repeat emails from the same company that
# also lack a title, instead of creating a new application row each time.
UNSPECIFIED_JOB_TITLE = "(unspecified role)"

# The model is explicitly told not to invent placeholder text, but does so
# anyway often enough in practice (seen for real: "not specified", "unknown",
# "n/a") that we normalize known variants defensively rather than trust the
# instruction alone. Checked case-insensitively.
_PLACEHOLDER_JOB_TITLES = {"not specified", "unknown", "n/a", "none", "null", "unspecified"}


def _normalize_job_title(job_title: str | None) -> str:
    if not job_title or job_title.strip().lower() in _PLACEHOLDER_JOB_TITLES:
        return UNSPECIFIED_JOB_TITLE
    return job_title

_CLASSIFY_AND_EXTRACT_PROMPT = """Triage this email for a personal job application tracker.

IMPORTANT: some emails contain a "similar jobs" / "you might also like" / "boost your chances" /
"Aehnliche Jobs" recommendation section, usually near the end, listing OTHER unrelated companies
and job postings. Never extract company_name or job_title from a recommendations/suggestions
section - only from the part of the email that is actually about the application you submitted.
If the real confirmation text itself never states the company, leave company_name null rather
than using a name that only appears in a recommendations section.

STEP 1 - is_relevant: True only if a real application was actually SUBMITTED and this email is a
confirmation or status update about it. False for job alert digests, newsletters, marketing, or
reminders about an INCOMPLETE / not-yet-submitted draft application - in any language (English
e.g. "finish your application", "your application is incomplete"; German e.g. "Bewerbung
abschliessen", "noch nicht abgeschlossen", "vergiss nicht deine Bewerbung", "weitermachen, wo du
aufgehoert hast"). If the email is nudging you to go back and complete a draft, nothing was
actually submitted yet, so mark is_relevant=False.

STEP 2 - company_name (REQUIRED whenever is_relevant is True, the most important field): the
actual employer/hiring company, from the actual confirmation text, sender display name/domain,
subject line, or signature - not from a recommendations section (see IMPORTANT above). Never use
the name of the job board/platform itself (e.g. StepStone, Indeed, LinkedIn, Personio) as
company_name, that is the messenger, not the employer. Leave company_name null if the real
confirmation text genuinely never states the actual employer anywhere, do not fall back to the
platform's name just to fill the field.

STEP 3 - job_title: the role applied for, or null if the email genuinely never repeats it (do not
invent placeholder text like "not specified").

STEP 4 - status: the MOST CONSERVATIVE option the email text unambiguously supports. Default to
"applied" for a generic acknowledgement ("we received it", "we will review and contact you") with
no concrete next stage stated - do not infer a positive or negative outcome from polite language.
- applied: received/sent/submitted, no further update yet (the default)
- viewed: explicitly says a recruiter opened/viewed it, nothing more
- assessment: invites an online test/quiz (not the same as an interview)
- interview: explicitly schedules/invites a live interview/call with a person
- rejected: explicitly states you were not selected
- offer: explicitly states you were offered the position
- other: relevant but doesn't fit above, or you're not confident which stage

Also extract job_url, location, and salary_text if mentioned, else null.

Platform hint from the sender address (may be wrong or absent): {platform_hint}

From: {sender}
Subject: {subject}

Body:
{body}"""


def make_classify_and_extract_node(model, sources: SourcesConfig):
    # NVIDIA's free-tier NIM endpoints can return a transient 503
    # ("Worker local total request limit reached") under shared load;
    # with_retry is LangChain's built-in backoff, not a hand-rolled loop.
    structured_model = model.with_structured_output(ClassifyAndExtractResult).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )

    def classify_and_extract(state: EmailState) -> dict:
        email = state["email"]
        platform_hint = guess_platform(email.sender, sources)
        prompt = _CLASSIFY_AND_EXTRACT_PROMPT.format(
            platform_hint=platform_hint or "unknown",
            sender=email.sender,
            subject=email.subject,
            body=email.body[:4000],
        )

        try:
            result = structured_model.invoke([HumanMessage(content=prompt)])
        except Exception as exc:  # noqa: BLE001 - LLM/parse failures must not crash the run
            logger.warning("classify+extract failed for message %s: %s", email.message_id, exc)
            result = None

        if result is None:
            return {
                "classification": "relevant",
                "platform_hint": platform_hint,
                "extracted": None,
                "error": "extraction_failed: model returned no result",
            }

        if not result.is_relevant:
            return {"classification": "irrelevant", "platform_hint": platform_hint, "extracted": None, "error": None}

        if not result.company_name:
            return {
                "classification": "relevant",
                "platform_hint": platform_hint,
                "extracted": None,
                "error": "missing_required_fields",
            }

        extracted = JobApplicationEvent(
            company_name=result.company_name,
            job_title=_normalize_job_title(result.job_title),
            status=result.status or "other",
            job_url=result.job_url,
            location=result.location,
            salary_text=result.salary_text,
        )
        return {"classification": "relevant", "platform_hint": platform_hint, "extracted": extracted, "error": None}

    return classify_and_extract


def make_match_node(session: Session):
    def match_existing_application(state: EmailState) -> dict:
        extracted = state["extracted"]
        platform = state.get("platform_hint") or "other"

        existing = repo.find_matching_application(
            session,
            company_name=extracted.company_name,
            job_title=extracted.job_title,
            platform=platform,
        )
        if existing is None:
            return {"match": MatchDecision(action="new_application")}
        return {"match": MatchDecision(action="update_existing", application_id=existing.id)}

    return match_existing_application


def make_upsert_node(session: Session, *, run_id: str):
    def upsert_db(state: EmailState) -> dict:
        email = state["email"]
        extracted = state["extracted"]
        match = state["match"]
        platform = state.get("platform_hint") or "other"
        event_date = _parse_email_date(email.date)

        if match.action == "new_application":
            application = repo.create_application(
                session,
                company_name=extracted.company_name,
                job_title=extracted.job_title,
                platform=platform,
                applied_date=event_date.date(),
                current_status=extracted.status,
                job_url=extracted.job_url,
                location=extracted.location,
                salary_text=extracted.salary_text,
            )
            repo.add_status_event(
                session,
                application_id=application.id,
                status=extracted.status,
                event_date=event_date,
                source_email_id=email.message_id,
                raw_extract_json=extracted.model_dump_json(),
            )
        elif match.action == "update_existing":
            repo.add_status_event(
                session,
                application_id=match.application_id,
                status=extracted.status,
                event_date=event_date,
                source_email_id=email.message_id,
                raw_extract_json=extracted.model_dump_json(),
            )
        # duplicate_skip: nothing to write, just fall through to mark_processed.

        repo.mark_processed(session, email.message_id, classification="relevant", pipeline_run_id=run_id)
        return {}

    return upsert_db


def make_skip_node(session: Session, *, run_id: str, classification: str):
    """Used for both the irrelevant branch and the extraction-failure branch:
    marks the email processed without writing any application/event rows, so
    it is never retried, but keeps a record of why it was skipped.
    """

    def mark_skipped(state: EmailState) -> dict:
        email = state["email"]
        repo.mark_processed(session, email.message_id, classification=classification, pipeline_run_id=run_id)
        return {}

    return mark_skipped


def _parse_email_date(date_header: str) -> datetime:
    try:
        return parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
