from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

from langchain_core.messages import HumanMessage
from sqlmodel import Session

from applysync.config import SourcesConfig, get_settings
from applysync.db import repository as repo
from applysync.gmail.models import RawEmail
from applysync.gmail.query_builder import guess_platform
from applysync.pipeline.sanitize import INJECTION_GUARD, fence
from applysync.pipeline.state import (
    ClassifyAndExtractResult,
    EmailState,
    JobApplicationEvent,
    MatchDecision,
    RelevanceOnlyResult,
)

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
_PLACEHOLDER_JOB_TITLES = {
    "not specified", "unknown", "n/a", "none", "null", "unspecified",
    # A UI button/CTA label ("Join our Talent Pool") that bled into the
    # extracted body text right where a real title would be, found by the
    # eval harness - not an actual role.
    "join our talent pool", "join talent pool",
}

# Defense-in-depth for the same failure the STEP 3 prompt guidance targets:
# despite being told not to, the model sometimes extracts the TYPE of
# interview/process step (real examples: "Technical Interview", "AI
# interview", "AI-powered video interview", "Online Assessment") as the
# job_title, rather than the actual role. Matches only when the ENTIRE title
# is qualifier + process words, so a real title that happens to contain one
# of these words (e.g. "AI Integration Engineer") is untouched - the
# "engineer" noun keeps it from matching the pattern below.
_PROCESS_STEP_JOB_TITLE_RE = re.compile(
    r"^((technical|phone|video|online|initial|first\s*round|ai[- ]?(powered)?)\s+)*"
    r"(interview|assessment|screening|call)s?$",
    re.IGNORECASE,
)


def _normalize_job_title(job_title: str | None) -> str:
    if not job_title:
        return UNSPECIFIED_JOB_TITLE
    stripped = job_title.strip()
    if stripped.lower() in _PLACEHOLDER_JOB_TITLES:
        return UNSPECIFIED_JOB_TITLE
    if _PROCESS_STEP_JOB_TITLE_RE.match(stripped):
        return UNSPECIFIED_JOB_TITLE
    return job_title


# Same defensive normalization as job_title, for the same reason: the model
# is told to leave company_name null when the email genuinely never states
# the employer, but sometimes emits a literal placeholder word instead (seen
# for real via the eval harness: the string "unknown"). Unlike job_title,
# there is no dedicated sentinel to fall back to - null IS the correct
# "genuinely not stated" value here (see classify_and_extract's
# missing_required_fields path), so this just prevents a placeholder word
# from masquerading as a real company.
_PLACEHOLDER_COMPANY_NAMES = {"unknown", "n/a", "none", "null", "not specified", "not stated"}


def _normalize_company_name(company_name: str | None) -> str | None:
    if not company_name:
        return None
    if company_name.strip().lower() in _PLACEHOLDER_COMPANY_NAMES:
        return None
    return company_name

_CLASSIFY_AND_EXTRACT_PROMPT = """Triage this email for a personal job application tracker.

IMPORTANT: some emails contain a "similar jobs" / "you might also like" / "boost your chances" /
"Aehnliche Jobs" recommendation section, usually near the end, listing OTHER unrelated companies
and job postings. Never extract company_name or job_title from a recommendations/suggestions
section - only from the part of the email that is actually about the application you submitted.
If the real confirmation text itself never states the company, leave company_name null rather
than using a name that only appears in a recommendations section.

STEP 1 - is_relevant: True whenever a real JOB application was actually SUBMITTED and this email
is ANY status update about it - a REJECTION is just as relevant as an acceptance or interview
invite. "we have decided to proceed with other candidates", "we regret to inform you", "leider
absagen", "nach sorgfaeltiger Pruefung ... andere Bewerber" are all real status updates about a
submitted job application, NOT a reason to mark the email irrelevant. is_relevant is only about
whether a JOB application was submitted and this email concerns it - it has nothing to do with
whether the news is good or bad (that judgment belongs in STEP 4's status field, not here).
False for a submission/confirmation/status update about anything OTHER than a job application
(e.g. an apartment rental application, a visa application, a loan application) even though the
generic word "application" appears - this tracker is for job applications only.
False also for a recruiting agency/platform/tool writing about ITSELF matching or reviewing you
(e.g. "we're Clera, we connect engineers with great companies", "let's find the right fit",
"we'll match you with opportunities") when no SPECIFIC real employer/role has actually been
confirmed - that is the recruiter's own outreach or intake process, not a status update about a
submitted job application to an actual employer.
False for job alert digests, newsletters, marketing, or reminders about an INCOMPLETE /
not-yet-submitted draft application - in any language (English e.g. "finish your application",
"your application is incomplete"; German e.g. "Bewerbung abschliessen", "noch nicht
abgeschlossen", "vergiss nicht deine Bewerbung", "weitermachen, wo du aufgehoert hast"). If the
email is nudging you to go back and complete a draft, nothing was actually submitted yet, so mark
is_relevant=False.

STEP 2 - company_name (REQUIRED whenever is_relevant is True, the most important field): the
actual employer/hiring company, from the actual confirmation text, sender display name/domain,
subject line, or signature - not from a recommendations section (see IMPORTANT above). Never use
the name of the job board/platform itself (e.g. StepStone, Indeed, LinkedIn, Personio) as
company_name, that is the messenger, not the employer. Leave company_name null if the real
confirmation text genuinely never states the actual employer anywhere, do not fall back to the
platform's name just to fill the field.

STEP 3 - job_title: the role YOU applied for, or null if the email genuinely never repeats it (do
not invent placeholder text like "not specified"). Two real mistakes to avoid:
- Never extract the TYPE of process step (e.g. "Technical Interview", "Phone Screening",
  "AI-powered video interview", "Online Assessment") as the job_title - that describes a STAGE of
  the process, not the role itself.
- Never extract a PERSON's own job title from a signature or "who you'll speak with" line (e.g. an
  email signed "Jane Doe, Werkstudentin People Operations, HR" or "your recruiter, Talent
  Acquisition") - that is the interviewer/recruiter's role, not the role you applied for.
If the actual applied-for role is not restated in this email, leave job_title null - do not
substitute either of the above.

STEP 4 - status: the MOST CONSERVATIVE option the email text unambiguously supports. Default to
"applied" for a generic acknowledgement ("we received it", "we will review and contact you") with
no concrete next stage stated - do not infer a positive or negative outcome from polite language.
- applied: received/sent/submitted, no further update yet (the default)
- viewed: explicitly says a recruiter opened/viewed it, nothing more
- assessment: invites an online test/quiz (not the same as an interview)
- interview: invites you to a live interview, call, screening, or introductory
  conversation/meeting with a person to discuss the role - a first-round "let's
  have a conversation" / "invite you to a first call" / "meeting to discuss"
  counts as interview, NOT applied. An email offering a booking link to schedule
  such a conversation counts too.
- rejected: explicitly states you were not selected
- offer: explicitly states you were offered the position
- other: relevant but doesn't fit above, or you're not confident which stage

Also extract job_url, location, and salary_text if mentioned, else null.

{injection_guard}

Platform hint from the sender address (may be wrong or absent): {platform_hint}

{email_block}"""


# Strong negative markers seen in real job-alert/digest emails - these
# arrived once the Gmail-side keyword filter was broadened to single words
# (see config/sources.yaml), which otherwise let recommendation emails
# through to the (LLM-rate-limited) classify_and_extract stage.
_REJECT_MARKERS = [
    "new jobs matching",
    "jobs for you",
    "recommended jobs",
    "job alert",
    "jobs you might like",
    "weekly digest",
    "similar jobs",
    "boost your chances",
    "unsubscribe from job alerts",
]

# The original, narrow confirmation phrases - reliable enough on their own
# that scrutiny adds nothing for them. Kept as a fixed list here rather than
# read from sources.yaml, since that file's confirmation_keywords now also
# includes the broadened single-word terms this heuristic exists to
# scrutinize; conflating the two would defeat the point.
_NARROW_CONFIRMATION_PHRASES = [
    "thank you for applying",
    "thank you for your application",
    "thank you for your interest in",
    "application received",
    "we have received your application",
    "we've received your application",
    "your application has been received",
    "your application for",
    "your application at",
    "application submitted",
    "successfully submitted",
    "application confirmation",
    "bewerbung",
    "beworben",
]


def _heuristic_scrutinize(email: RawEmail) -> Literal["pass", "reject", "ambiguous"]:
    subject_lower = email.subject.lower()
    body_prefix_lower = email.body[:500].lower()
    text = f"{subject_lower} {body_prefix_lower}"

    # Pass is checked BEFORE reject: a real Wolters Kluwer confirmation
    # ("your job application ... has been successfully submitted") was
    # wrongly scrutinized away because its OWN footer boilerplate ("manage
    # job alerts / create job alerts", generic candidate-portal navigation)
    # contains the substring "job alert" - one of the reject markers below -
    # so reject fired before the confirmation phrase was ever checked. A
    # narrow, high-precision confirmation match is strong enough evidence to
    # win over an incidental reject-marker match in unrelated boilerplate;
    # checked against the body prefix too, not just the subject, since a
    # real confirmation phrase can appear in either.
    if any(phrase in subject_lower or phrase in body_prefix_lower for phrase in _NARROW_CONFIRMATION_PHRASES):
        return "pass"
    if any(marker in text for marker in _REJECT_MARKERS):
        return "reject"
    return "ambiguous"


_RELEVANCE_ONLY_PROMPT = """Is this email about a job application the user personally submitted \
(a confirmation, status update, interview invite, rejection, or offer) - as opposed to a job \
alert digest, newsletter, or job recommendation email?

{injection_guard}

{email_block}"""


def make_scrutinize_relevance_node(model, sources: SourcesConfig, *, escalation_model=None):
    """Entry-point node: a heuristic pre-filter in front of the (rate-limited)
    classify_and_extract call. Clear passes/rejects cost 0 extra LLM calls;
    only the genuinely ambiguous middle bucket costs one cheap extra call -
    this is what keeps a broadened Gmail-side keyword filter from
    multiplying sync time by its false-positive rate.

    escalation_model, if given, handles that one ambiguous-case call instead
    of model: the heuristic already screened out every case cheap/clear
    enough for the fast model to be worth trusting, so the rare remaining
    call is exactly where a larger, more careful model earns its slower
    latency. Falls back to model when no escalation model is configured.
    """
    llm = escalation_model or model
    structured_model = llm.with_structured_output(RelevanceOnlyResult).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )

    def scrutinize_relevance(state: EmailState) -> dict:
        email = state["email"]
        heuristic = _heuristic_scrutinize(email)
        if heuristic in ("pass", "reject"):
            return {"scrutiny": heuristic}

        email_block = fence(
            f"From: {email.sender}\nSubject: {email.subject}\n\nBody (truncated):\n{email.body[:1000]}",
            "untrusted_email",
        )
        prompt = _RELEVANCE_ONLY_PROMPT.format(
            injection_guard=INJECTION_GUARD, email_block=email_block
        )
        try:
            result = structured_model.invoke([HumanMessage(content=prompt)])
        except Exception as exc:  # noqa: BLE001 - fail open, don't drop a possibly-real email
            logger.warning(
                "scrutiny LLM call failed for message %s, failing open (pass): %s", email.message_id, exc
            )
            return {"scrutiny": "pass"}

        return {"scrutiny": "pass" if result.is_relevant else "reject"}

    return scrutinize_relevance


def make_classify_and_extract_node(model, sources: SourcesConfig, *, escalation_model=None):
    """escalation_model, if given, gets ONE retry with the exact same prompt
    when the fast model either fails outright (call/parse error) or comes
    back without a usable company_name - the two concrete, unambiguous
    failure signals already surfaced by this node's own logic, not a new
    confidence judgment asked of the model. Deliberately narrow: this is not
    called for every email, only the minority the fast model already
    couldn't handle, keeping the rate-limited call volume close to what it
    was before escalation existed.
    """
    # NVIDIA's free-tier NIM endpoints can return a transient 503
    # ("Worker local total request limit reached") under shared load;
    # with_retry is LangChain's built-in backoff, not a hand-rolled loop.
    structured_model = model.with_structured_output(ClassifyAndExtractResult).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )
    escalation_structured_model = (
        escalation_model.with_structured_output(ClassifyAndExtractResult).with_retry(
            stop_after_attempt=5, wait_exponential_jitter=True
        )
        if escalation_model is not None
        else None
    )

    def _invoke(structured, prompt, message_id):
        try:
            return structured.invoke([HumanMessage(content=prompt)])
        except Exception as exc:  # noqa: BLE001 - LLM/parse failures must not crash the run
            logger.warning("classify+extract failed for message %s: %s", message_id, exc)
            return None

    def classify_and_extract(state: EmailState) -> dict:
        email = state["email"]
        platform_hint = guess_platform(email.sender, sources)
        email_block = fence(
            f"From: {email.sender}\nSubject: {email.subject}\n\nBody:\n{email.body[:4000]}",
            "untrusted_email",
        )
        prompt = _CLASSIFY_AND_EXTRACT_PROMPT.format(
            platform_hint=platform_hint or "unknown",
            injection_guard=INJECTION_GUARD,
            email_block=email_block,
        )

        result = _invoke(structured_model, prompt, email.message_id)
        needs_escalation = result is None or (
            result.is_relevant and not _normalize_company_name(result.company_name)
        )
        if needs_escalation and escalation_structured_model is not None:
            logger.info("classify+extract escalating message %s to the larger model", email.message_id)
            escalated = _invoke(escalation_structured_model, prompt, email.message_id)
            if escalated is not None:
                result = escalated

        if result is None:
            return {
                "classification": "relevant",
                "platform_hint": platform_hint,
                "extracted": None,
                "error": "extraction_failed: model returned no result",
            }

        if not result.is_relevant:
            return {"classification": "irrelevant", "platform_hint": platform_hint, "extracted": None, "error": None}

        company_name = _normalize_company_name(result.company_name)
        if not company_name:
            return {
                "classification": "relevant",
                "platform_hint": platform_hint,
                "extracted": None,
                "error": "missing_required_fields",
            }

        extracted = JobApplicationEvent(
            company_name=company_name,
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

        existing = repo.find_matching_application(
            session,
            company_name=extracted.company_name,
            job_title=extracted.job_title,
        )
        if existing is not None:
            # Exact (normalized) company+title hit: the clear update case, no
            # agent needed. Platform is not part of identity (see
            # repo.find_matching_application).
            return {"match": MatchDecision(action="update_existing", application_id=existing.id)}

        candidates = repo.find_candidate_applications(
            session, company_name=extracted.company_name
        )
        if not candidates:
            # No prior application for this company at all: clearly new.
            return {"match": MatchDecision(action="new_application")}

        # Ambiguous: the company already exists but no title matched exactly
        # (the documented missing-title-vs-different-title gap). Leave `match`
        # unset and surface the candidate ids so the graph routes to the
        # disambiguation agent instead of blindly creating a new row.
        return {"candidate_ids": [c.id for c in candidates], "match": None}

    return match_existing_application


# Confidence levels the agent can return, ordered so a numeric compare decides
# whether a merge verdict clears the auto-apply bar (see disambiguate_match).
_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _merge_below_bar(confidence: str, min_confidence: str) -> bool:
    """True when a same_application/duplicate verdict at `confidence` is below
    the configured auto-merge bar, so it must be routed to human review instead
    of applied silently. Unknown values sort as low (safest: route to review).
    """
    have = _CONFIDENCE_ORDER.get((confidence or "low").lower(), 0)
    need = _CONFIDENCE_ORDER.get((min_confidence or "medium").lower(), 1)
    return have < need


def make_disambiguate_node(
    session: Session,
    *,
    model,
    gmail_client,
    search_client,
    escalation_model=None,
    agent_model=None,
    min_auto_merge_confidence: str | None = None,
):
    """LLM tool-loop agent for the ambiguous match case (see
    research/disambiguate.py). Fails OPEN to a new application on any agent
    error - a possibly-redundant row is recoverable, wrongly merging two
    distinct applications is not. Only ever reached when match_existing_
    application left `match` unset with candidate_ids set.

    escalation_model, if given, always runs this agent instead of model: unlike
    scrutiny/classify_and_extract this node is already low-volume (only the
    genuinely ambiguous match cases, ~50 calls per full sync vs. 500), so there's
    no fast-path cost to always giving it the more careful model. Falls back to
    model when no escalation model is configured.

    agent_model, if given, takes precedence over escalation_model for THIS node
    only (the disambiguation agent), leaving scrutiny/classify_and_extract's
    escalation retries on the NVIDIA escalation model. This is the hybrid path
    (see llm.get_agent_model): route just the agent to Groq for its own rate
    budget and lower latency, without changing extraction.

    min_auto_merge_confidence gates when a same_application/duplicate verdict is
    applied directly vs. routed to human review (M5 confidence-routed merges).
    Defaults to settings.disambiguation_min_auto_merge_confidence; a verdict
    below it is written as a NEW application and a merge ReviewSuggestion is
    queued instead of merging silently.
    """
    from applysync.research.disambiguate import DisambiguationError, run_disambiguation

    # Run on the Groq agent model when configured, falling back to the NVIDIA
    # escalation model on failure; otherwise just use the best NVIDIA model.
    llm = agent_model or escalation_model or model
    fallback = escalation_model if agent_model else None
    min_confidence = min_auto_merge_confidence or get_settings().disambiguation_min_auto_merge_confidence

    def disambiguate_match(state: EmailState) -> dict:
        extracted = state["extracted"]
        email = state["email"]
        candidate_ids = state.get("candidate_ids") or []
        candidates = [
            app
            for cid in candidate_ids
            if (app := repo.get_application(session, cid)) is not None
        ]
        if not candidates:
            return {"match": MatchDecision(action="new_application")}

        try:
            verdict = run_disambiguation(
                email,
                extracted,
                candidates,
                session=session,
                gmail_client=gmail_client,
                search_client=search_client,
                model=llm,
                fallback_model=fallback,
            )
        except DisambiguationError as exc:
            logger.warning("Disambiguation failed, defaulting to new application: %s", exc)
            return {"match": MatchDecision(action="new_application")}
        except Exception:  # noqa: BLE001 - any agent/transport failure fails open
            logger.exception("Disambiguation agent crashed, defaulting to new application")
            return {"match": MatchDecision(action="new_application")}

        is_merge = (
            verdict.decision in ("same_application", "duplicate")
            and verdict.matched_application_id
        )
        # A low-confidence merge is unrecoverable if wrong, so it is not applied
        # silently: write the email as a NEW application (recoverable, the same
        # choice the agent's own error path makes) and hand upsert_db the
        # candidate id so it queues a "merge into this?" ReviewSuggestion for a
        # human. Only merges clearing the bar auto-apply.
        if is_merge and _merge_below_bar(verdict.confidence, min_confidence):
            logger.info(
                "Disambiguation verdict %s (confidence=%s) below auto-merge bar %s, "
                "routing to review as a new application",
                verdict.decision, verdict.confidence, min_confidence,
            )
            return {
                "match": MatchDecision(action="new_application"),
                "review_merge_candidate_id": verdict.matched_application_id,
                "disambiguation_confidence": verdict.confidence,
                "disambiguation_reasoning": verdict.reasoning,
            }

        if verdict.decision == "same_application" and verdict.matched_application_id:
            match = MatchDecision(
                action="update_existing", application_id=verdict.matched_application_id
            )
        elif verdict.decision == "duplicate" and verdict.matched_application_id:
            match = MatchDecision(
                action="duplicate_skip", application_id=verdict.matched_application_id
            )
        else:
            match = MatchDecision(action="new_application")
        return {"match": match, "disambiguation_reasoning": verdict.reasoning}

    return disambiguate_match


def make_upsert_node(session: Session, *, run_id: str):
    def upsert_db(state: EmailState) -> dict:
        email = state["email"]
        extracted = state["extracted"]
        # A missing match means the ambiguous branch fell open (no agent wired
        # in, or candidate_ids surfaced without a resolved decision): default to
        # a new application, the same recoverable-over-destructive choice the
        # agent's own error path makes.
        match = state.get("match") or MatchDecision(action="new_application")
        platform = state.get("platform_hint") or "other"
        event_date = _parse_email_date(email.date)
        # Present only when this email went through the disambiguation agent;
        # stored on the event's notes so the dedupe/split decision is auditable.
        notes = state.get("disambiguation_reasoning")

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
                notes=notes,
            )
            # Confidence-routed merge: the agent thought this matched an existing
            # application but wasn't sure enough to merge automatically. The new
            # row above keeps the email tracked (recoverable); queue a suggestion
            # so a human can confirm collapsing it into the candidate (see
            # repo.approve_review_suggestion's merge_into path).
            candidate_id = state.get("review_merge_candidate_id")
            if candidate_id is not None:
                candidate = repo.get_application(session, candidate_id)
                if candidate is not None:
                    repo.create_review_suggestion(
                        session,
                        message_id=email.message_id,
                        action="merge_into",
                        application_id=candidate.id,
                        previous_classification="relevant",
                        suggested_classification="relevant",
                        previous_extract_json=json.dumps(
                            {
                                "company_name": candidate.company_name,
                                "job_title": candidate.job_title,
                                "status": candidate.current_status,
                                "platform": candidate.platform,
                            }
                        ),
                        suggested_extract_json=json.dumps(
                            {**extracted.model_dump(), "platform": platform}
                        ),
                        confidence=state.get("disambiguation_confidence"),
                        pipeline_run_id=run_id,
                    )
        elif match.action == "update_existing":
            repo.add_status_event(
                session,
                application_id=match.application_id,
                status=extracted.status,
                event_date=event_date,
                source_email_id=email.message_id,
                raw_extract_json=extracted.model_dump_json(),
                notes=notes,
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
