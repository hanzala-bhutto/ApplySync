from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from langchain_core.messages import HumanMessage
from sqlmodel import Session

from applysync.config import SourcesConfig
from applysync.db import repository as repo
from applysync.gmail.query_builder import guess_platform
from applysync.pipeline.state import EmailState, JobApplicationEvent, MatchDecision

logger = logging.getLogger(__name__)

# Canonical stand-in when an email genuinely doesn't mention a job title
# (some ATS confirmations don't repeat it). Using one fixed string, not
# whatever placeholder text the model might otherwise invent, is what lets
# find_matching_application dedupe repeat emails from the same company that
# also lack a title, instead of creating a new application row each time.
UNSPECIFIED_JOB_TITLE = "(unspecified role)"

_CLASSIFY_PROMPT = """You triage inbox emails for a personal job application tracker.

Decide whether this email is a genuine job-application confirmation or status
update (e.g. application received, under review, interview invite, rejection,
offer) as opposed to a job alert digest, newsletter, marketing email, or
anything unrelated to an application you personally submitted.

From: {sender}
Subject: {subject}

Body:
{body}

Respond with exactly one word: RELEVANT or IRRELEVANT."""

_EXTRACT_PROMPT = """Extract job application details from this email.

If a field is not mentioned in the email, leave it null, do not guess and do
not invent placeholder text such as "not specified" or "unknown" - null means
null. This matters especially for job_title: some confirmation emails never
repeat the job title, in which case it must be null, not a made-up string.

Platform hint from the sender address (may be wrong or absent): {platform_hint}

Subject: {subject}

Body:
{body}"""


def make_classify_node(model, sources: SourcesConfig):
    # NVIDIA's free-tier NIM endpoints can return a transient 503
    # ("Worker local total request limit reached") under shared load;
    # with_retry is LangChain's built-in backoff, not a hand-rolled loop.
    resilient_model = model.with_retry(stop_after_attempt=5, wait_exponential_jitter=True)

    def classify_relevant(state: EmailState) -> dict:
        email = state["email"]
        platform_hint = guess_platform(email.sender, sources)

        prompt = _CLASSIFY_PROMPT.format(
            sender=email.sender, subject=email.subject, body=email.body[:2000]
        )
        response = resilient_model.invoke([HumanMessage(content=prompt)])
        text = response.content.strip().upper()
        classification = "relevant" if "IRRELEVANT" not in text and "RELEVANT" in text else "irrelevant"

        return {"classification": classification, "platform_hint": platform_hint}

    return classify_relevant


def make_extract_node(model):
    structured_model = model.with_structured_output(JobApplicationEvent).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )

    def extract_structured_data(state: EmailState) -> dict:
        email = state["email"]
        prompt = _EXTRACT_PROMPT.format(
            platform_hint=state.get("platform_hint") or "unknown",
            subject=email.subject,
            body=email.body[:4000],
        )

        try:
            extracted = structured_model.invoke([HumanMessage(content=prompt)])
        except Exception as exc:  # noqa: BLE001 - LLM/parse failures must not crash the run
            logger.warning("extraction failed for message %s: %s", email.message_id, exc)
            return {"extracted": None, "error": f"extraction_failed: {exc}"}

        if not extracted.company_name:
            return {"extracted": None, "error": "missing_required_fields"}

        if not extracted.job_title:
            extracted = extracted.model_copy(update={"job_title": UNSPECIFIED_JOB_TITLE})

        return {"extracted": extracted, "error": None}

    return extract_structured_data


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
