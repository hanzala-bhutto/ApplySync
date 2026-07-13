from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from applysync.db import repository as repo
from applysync.db.models import Application
from applysync.gmail.models import RawEmail
from applysync.pipeline.state import DisambiguationVerdict, JobApplicationEvent
from applysync.search import SearxngError

logger = logging.getLogger(__name__)

# A single ambiguous email should never fan out into an unbounded number of
# rate-limited LLM calls (NVIDIA free tier is 40 RPM). This caps the agent's
# tool-gathering turns; if it hasn't submitted a verdict by then it's forced
# to decide from what it has (see run_disambiguation).
MAX_AGENT_TURNS = 6

# Source emails and search snippets can be long; the model only needs enough to
# tell "same application" from "different application", not the whole payload.
_EMAIL_TRUNCATE = 1500
_SEARCH_SNIPPET_TRUNCATE = 300


class DisambiguationError(RuntimeError):
    """Raised when the agent cannot produce a valid verdict (bad tool args,
    a hallucinated application id, or the model never submitting one). The
    caller (the disambiguate node) catches this and fails OPEN to a new
    application, mirroring how scrutinize_relevance fails open on an LLM error -
    creating a possibly-redundant row is recoverable; wrongly merging two
    distinct applications is not.
    """


_SYSTEM_PROMPT = """You are resolving whether a new job-application email refers \
to an application already on record, or a genuinely separate one.

This only runs for ambiguous cases: an application at the SAME company and \
platform already exists, but the job title does not match exactly (one may be \
missing a title, or the titles may genuinely differ). Your job is to decide, \
using the tools, then call submit_verdict.

The NEW email being classified:
  From: {sender}
  Subject: {subject}
  Date: {date}
  Extracted company: {company}
  Extracted job title: {job_title}
  Body (truncated):
  {body}

Existing candidate applications at this company/platform:
{candidates}

Use the tools to inspect a candidate's status history, read the source email a \
candidate came from (to compare against the new email above), or check the \
company on the web if real-world identity is in doubt. When you are confident, \
call submit_verdict exactly once with:
  - decision: "same_application" if the new email is a status update for one of \
the candidates; "different_application" if it is a genuinely separate role; \
"duplicate" if it is a redundant re-confirmation of a candidate that should NOT \
create a new row.
  - matched_application_id: the candidate id for same_application or duplicate; \
0 for different_application.
  - reasoning: one or two sentences on why.

Prefer "different_application" only when the evidence genuinely points to a \
separate role - a missing title alone is not proof of a different application."""


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "..."


def _format_candidates(candidates: list[Application]) -> str:
    lines = []
    for c in candidates:
        lines.append(
            f"  [id={c.id}] company={c.company_name!r} title={c.job_title!r} "
            f"status={c.current_status} applied={c.applied_date}"
        )
    return "\n".join(lines) if lines else "  (none)"


def run_disambiguation(
    current_email: RawEmail,
    extracted: JobApplicationEvent,
    candidates: list[Application],
    *,
    session,
    gmail_client,
    search_client,
    model,
) -> DisambiguationVerdict:
    """Hand-rolled LLM tool loop: the model chooses which tools to call to
    gather evidence, then submits a structured verdict via the submit_verdict
    tool. Returns a validated DisambiguationVerdict or raises
    DisambiguationError (the node fails open on that).

    A terminal-tool-call ends the loop rather than a plain-text parse: this
    model's native tool-calling is reliable for scalar args (verified), whereas
    with_structured_output returns empty on it - the same constraint that made
    company research use PydanticOutputParser. Here scalar tool args sidestep
    both.
    """
    candidate_by_id = {c.id: c for c in candidates}

    @tool
    def get_status_history(application_id: int) -> str:
        """Return the status-change history (status, date, source) for a candidate
        application id, oldest first."""
        if application_id not in candidate_by_id:
            return f"error: {application_id} is not one of the candidate ids"
        events = repo.application_timeline(session, application_id)
        if not events:
            return "no status events recorded"
        return "\n".join(
            f"{e.event_date}: {e.status}"
            + (" (manual)" if e.source_email_id is None else "")
            + (f" - {e.notes}" if e.notes else "")
            for e in events
        )

    @tool
    def read_source_email(application_id: int) -> str:
        """Read the email that most recently fed a candidate application, to
        compare it against the new email being classified. Returns subject, sender
        and a truncated body."""
        if application_id not in candidate_by_id:
            return f"error: {application_id} is not one of the candidate ids"
        events = repo.application_timeline(session, application_id)
        source_id = next(
            (e.source_email_id for e in reversed(events) if e.source_email_id), None
        )
        if source_id is None:
            return "no source email on record for this application (manual entry)"
        try:
            email = gmail_client.get_message(source_id)
        except Exception as exc:  # noqa: BLE001 - degrade, don't crash the agent
            logger.warning("read_source_email fetch failed for %s: %s", source_id, exc)
            return f"could not fetch source email: {exc}"
        return (
            f"From: {email.sender}\nSubject: {email.subject}\nDate: {email.date}\n"
            f"Body: {_truncate(email.body, _EMAIL_TRUNCATE)}"
        )

    @tool
    def web_entity_check(query: str) -> str:
        """Search the web to confirm a company's real-world identity when it is in
        doubt (e.g. two similarly-named companies). Returns titles and snippets."""
        try:
            results = search_client.search(query, max_results=4)
        except SearxngError as exc:
            logger.warning("web_entity_check search failed: %s", exc)
            return f"web search unavailable: {exc}"
        if not results:
            return "no results"
        return "\n".join(
            f"- {r.title}: {_truncate(r.content, _SEARCH_SNIPPET_TRUNCATE)} ({r.url})"
            for r in results
        )

    submitted: dict = {}

    @tool
    def submit_verdict(decision: str, matched_application_id: int, reasoning: str) -> str:
        """Submit the final decision. decision is one of same_application,
        different_application, duplicate. matched_application_id is the candidate id
        for same_application/duplicate, or 0 for different_application."""
        submitted["verdict"] = (decision, matched_application_id, reasoning)
        return "verdict recorded"

    tools = [get_status_history, read_source_email, web_entity_check, submit_verdict]
    tools_by_name = {t.name: t for t in tools}
    bound = model.bind_tools(tools).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )

    messages = [
        SystemMessage(
            content=_SYSTEM_PROMPT.format(
                sender=current_email.sender,
                subject=current_email.subject,
                date=current_email.date,
                company=extracted.company_name,
                job_title=extracted.job_title,
                body=_truncate(current_email.body, _EMAIL_TRUNCATE),
                candidates=_format_candidates(candidates),
            )
        ),
        HumanMessage(content="Decide whether this email matches an existing application."),
    ]

    for _ in range(MAX_AGENT_TURNS):
        response = bound.invoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            # The model answered in prose instead of calling a tool. Nudge once
            # by continuing the loop; a repeated non-call just exhausts turns.
            messages.append(
                HumanMessage(content="Call submit_verdict with your decision.")
            )
            continue

        for call in tool_calls:
            fn = tools_by_name.get(call["name"])
            if fn is None:
                output = f"error: unknown tool {call['name']}"
            else:
                output = fn.invoke(call["args"])
            messages.append(ToolMessage(content=str(output), tool_call_id=call["id"]))

        if "verdict" in submitted:
            return _build_verdict(*submitted["verdict"], candidate_by_id)

    raise DisambiguationError("agent did not submit a verdict within the turn limit")


def _build_verdict(
    decision: str, matched_application_id: int, reasoning: str, candidate_by_id: dict
) -> DisambiguationVerdict:
    matched_id = matched_application_id or None
    if decision in ("same_application", "duplicate"):
        if matched_id not in candidate_by_id:
            raise DisambiguationError(
                f"{decision} verdict referenced unknown application id "
                f"{matched_application_id!r}"
            )
    else:
        matched_id = None
    try:
        return DisambiguationVerdict(
            decision=decision, matched_application_id=matched_id, reasoning=reasoning
        )
    except Exception as exc:  # noqa: BLE001 - invalid enum/shape from the model
        raise DisambiguationError(f"invalid verdict: {exc}") from exc
