"""Tests for the entity/duplicate-resolution disambiguation agent and its
wiring into the pipeline graph. The agent's LLM is faked at the bind_tools
boundary (tests/fakes.py), never a real NVIDIA call; Gmail and SearXNG are
faked too. Covers: the tool loop itself, each verdict mapping onto a
MatchDecision, fail-open behavior, graph routing (ambiguous goes through the
agent, clear cases do not), and idempotency.
"""

from datetime import date, datetime

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import select

from applysync.config import get_sources
from applysync.db import repository as repo
from applysync.db.models import StatusEvent
from applysync.gmail.models import RawEmail
from applysync.pipeline.graph import process_emails
from applysync.pipeline.nodes import UNSPECIFIED_JOB_TITLE
from applysync.pipeline.state import ClassifyAndExtractResult, JobApplicationEvent
from applysync.research.disambiguate import (
    MAX_AGENT_TURNS,
    DisambiguationError,
    _build_verdict,
    _extract_req_ids,
    run_disambiguation,
)
from tests.fakes import (
    FakeAIResponse,
    FakeExtractAndToolModel,
    FakeGmailClient,
    FakeSearchClient,
    FakeStructuredModel,
    FakeToolLoopModel,
)

_RAW_MESSAGE = {
    "id": "seed-1",
    "threadId": "t-seed-1",
    "payload": {
        "headers": [
            {"name": "From", "value": "jobs@nagarro.com"},
            {"name": "Subject", "value": "Your application at Nagarro"},
            {"name": "Date", "value": "Tue, 1 Jun 2026 09:00:00 +0000"},
        ],
        "mimeType": "text/plain",
        "body": {"data": "VGhhbmsgeW91IGZvciBhcHBseWluZy4="},  # "Thank you for applying."
    },
}


def _email(message_id="msg-1", sender="jobs-noreply@linkedin.com", subject="Your application was sent", body="body"):
    return RawEmail(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender=sender,
        subject=subject,
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body=body,
    )


def _seed_application(session, *, company="Nagarro", title="Engineer", platform="linkedin", status="applied"):
    app = repo.create_application(
        session,
        company_name=company,
        job_title=title,
        platform=platform,
        applied_date=date(2026, 6, 1),
        current_status=status,
    )
    repo.add_status_event(
        session,
        application_id=app.id,
        status=status,
        event_date=datetime(2026, 6, 1, 9, 0, 0),
        source_email_id="seed-1",
    )
    return app


def _verdict_call(
    decision, matched_application_id, reasoning="because", call_id="c1", confidence="high"
):
    return FakeAIResponse(
        tool_calls=[
            {
                "name": "submit_verdict",
                "args": {
                    "decision": decision,
                    "matched_application_id": matched_application_id,
                    "reasoning": reasoning,
                    "confidence": confidence,
                },
                "id": call_id,
            }
        ]
    )


def _extract_model(agent_script, *, company="Nagarro", job_title=None, status="applied"):
    """A whole-graph model: structured classify+extract returns the given
    extraction, and the bind_tools loop plays agent_script."""
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name=company, job_title=job_title, status=status
    )
    return FakeExtractAndToolModel(FakeStructuredModel(result=result), agent_script)


_run_counter = 0


def _run_ambiguous(session, model, *, message_id="msg-2"):
    global _run_counter
    _run_counter += 1
    run_id = f"run-{_run_counter}"
    repo.create_pipeline_run(session, run_id)
    return process_emails(
        [_email(message_id=message_id, subject="Update on your application")],
        model=model,
        session=session,
        sources=get_sources(),
        run_id=run_id,
        checkpointer=MemorySaver(),
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
    )


# --- run_disambiguation: the tool loop itself -------------------------------


def _current_email():
    return _email(message_id="msg-2", subject="Update on your application")


def _extracted(company="Nagarro", job_title=None):
    return JobApplicationEvent(company_name=company, job_title=job_title, status="applied")


def test_agent_can_call_a_tool_before_submitting(session):
    """Multi-turn: the model inspects a candidate's history, then submits."""
    app = _seed_application(session)
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id),
        ]
    )

    verdict = run_disambiguation(
        _current_email(),
        _extracted(),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert model.invocations == 2
    assert verdict.decision == "same_application"


def test_agent_recovers_from_bad_tool_args_instead_of_crashing(session):
    """Regression: this model passed a Gmail message-id STRING as the int
    application_id to read_source_email, and the unguarded tool call crashed the
    whole agent, failing open to a duplicate. A bad arg must now come back to
    the model as an error so it can correct and still submit a verdict.
    """
    app = _seed_application(session)
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[
                    {"name": "read_source_email", "args": {"application_id": "9543a4a3f5-not-an-int"}, "id": "t1"}
                ]
            ),
            FakeAIResponse(
                tool_calls=[{"name": "read_source_email", "args": {"application_id": app.id}, "id": "t2"}]
            ),
            _verdict_call("same_application", app.id),
        ]
    )

    verdict = run_disambiguation(
        _current_email(),
        _extracted(),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert model.invocations == 3  # errored call, corrected call, then a successful verdict
    assert verdict.decision == "same_application"


def test_title_less_email_prompt_biases_toward_most_recent_active_candidate(session):
    """Regression test for a real bug: a title-less status email ("Your
    Interview Appointment") got attached to the wrong candidate, with the
    model hallucinating the interviewer's title as the job. The prompt sent
    to the agent must call out the single most-recently-updated open
    candidate as a weak prior when the new email names no title at all.
    """
    stale = _seed_application(session, title="Backend Engineer", status="rejected")
    active = _seed_application(session, title="Frontend Engineer", status="interview")
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": active.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", active.id),
        ]
    )

    run_disambiguation(
        _current_email(),
        _extracted(job_title=UNSPECIFIED_JOB_TITLE),
        [stale, active],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    system_prompt = model.seen_messages[0][0].content
    assert "does not name a job title" in system_prompt
    assert "likeliest match" in system_prompt
    assert f"id={active.id} " in system_prompt
    assert "was updated most recently" in system_prompt


def test_title_less_email_prompt_has_no_single_lead_when_no_active_candidate(session):
    """All candidates rejected/declined: there's no obvious lead, so the
    prompt should push toward different_application rather than naming any
    one candidate as the likely match.
    """
    stale = _seed_application(session, title="Backend Engineer", status="rejected")
    model = FakeToolLoopModel([_verdict_call("different_application", 0)])

    run_disambiguation(
        _current_email(),
        _extracted(job_title=UNSPECIFIED_JOB_TITLE),
        [stale],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    system_prompt = model.seen_messages[0][0].content
    assert "does not name a job title" in system_prompt
    assert "likeliest match" not in system_prompt
    assert "prefer \"different_application\"" in system_prompt


def test_normal_title_email_has_no_title_less_guidance(session):
    app = _seed_application(session)
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id),
        ]
    )

    run_disambiguation(
        _current_email(),
        _extracted(job_title="Engineer"),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    system_prompt = model.seen_messages[0][0].content
    assert "does not name a job title" not in system_prompt


def test_hallucinated_id_raises_in_build_verdict(session):
    """_build_verdict's own unknown-id validation, tested directly: with the
    evidence-gathering requirement in place (see
    test_submit_verdict_rejected_without_evidence_for_that_id), a hallucinated
    id can no longer reach this point via the normal tool loop at all - the
    gate in submit_verdict rejects it first, since evidence can only ever be
    gathered for a REAL candidate id (get_status_history/read_source_email
    themselves reject an unknown id before recording it as evidence). This
    keeps _build_verdict's check as defense in depth for any future caller.
    """
    app = _seed_application(session)

    with pytest.raises(DisambiguationError):
        _build_verdict("same_application", 999, "because", "high", {app.id: app})


def test_submit_verdict_rejected_without_evidence_for_that_id(session):
    """The core reliability fix: a same_application verdict for a candidate
    the agent never actually looked at (no get_status_history/read_source_email
    call for THAT id) is rejected, forcing the agent to gather evidence or
    exhaust its turns and fail open - it can no longer merge from a plausible-
    sounding guess alone.
    """
    app = _seed_application(session)
    model = FakeToolLoopModel([_verdict_call("same_application", app.id)] * MAX_AGENT_TURNS)

    with pytest.raises(DisambiguationError):
        run_disambiguation(
            _current_email(),
            _extracted(),
            [app],
            session=session,
            gmail_client=FakeGmailClient(_RAW_MESSAGE),
            search_client=FakeSearchClient(results=[]),
            model=model,
        )


def test_submit_verdict_accepted_after_gathering_evidence_for_that_id(session):
    app = _seed_application(session)
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id),
        ]
    )

    verdict = run_disambiguation(
        _current_email(),
        _extracted(),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert verdict.decision == "same_application"
    assert verdict.matched_application_id == app.id


def test_different_application_verdict_needs_no_evidence(session):
    """different_application (creating a new row, not merging) is always
    safe to submit directly - the evidence gate only applies to
    same_application/duplicate."""
    app = _seed_application(session)
    model = FakeToolLoopModel([_verdict_call("different_application", 0)])

    verdict = run_disambiguation(
        _current_email(),
        _extracted(),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert verdict.decision == "different_application"


def test_prompt_warns_against_dismissing_substantive_title_differences(session):
    """Regression: a real full-history resync showed the agent merging
    genuinely different roles at one company ("Backend Engineer - Pricing",
    "Software Engineer - Trading Systems", "Werkstudentin People Operations,
    HR") into one application, rationalizing each as "the title differing
    slightly". Structural check that the sharpened guidance survives future
    prompt edits.
    """
    app = _seed_application(session)
    model = FakeToolLoopModel([_verdict_call("different_application", 0)])

    run_disambiguation(
        _current_email(),
        _extracted(),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    system_prompt = model.seen_messages[0][0].content
    assert "company and date alone are NOT sufficient evidence" in system_prompt
    assert "different specialization, department, or seniority track" in system_prompt


def test_agent_never_submitting_raises_after_turn_limit(session):
    app = _seed_application(session)
    prose = [FakeAIResponse(content="thinking...") for _ in range(MAX_AGENT_TURNS)]
    model = FakeToolLoopModel(prose)

    with pytest.raises(DisambiguationError):
        run_disambiguation(
            _current_email(),
            _extracted(),
            [app],
            session=session,
            gmail_client=FakeGmailClient(_RAW_MESSAGE),
            search_client=FakeSearchClient(results=[]),
            model=model,
        )


# --- requisition-ID short-circuit (deterministic, before any model call) ----


def test_extract_req_ids_matches_five_to_eight_digits_only():
    """ATS req IDs are 5-8 digit numbers; 4-digit years and 9+ digit strings
    are deliberately excluded, and multiple ids in one text are all pulled."""
    assert _extract_req_ids("Requisition 453330 for the role") == {"453330"}
    assert _extract_req_ids("you applied in 2026") == set()  # 4 digits (year)
    assert _extract_req_ids("tracking id 123456789") == set()  # 9 digits, too long
    assert _extract_req_ids("REQ-12345 / ref 87654321") == {"12345", "87654321"}
    assert _extract_req_ids(None, "", "no digits at all") == set()


def test_shared_requisition_id_short_circuits_to_same_application_without_model(session):
    """A 5-8 digit ATS req ID shared between the new email and exactly one
    candidate is an exact same-posting signal, decided in Python before the
    model runs at all (the model got this wrong even with the ID in front of
    it). The empty model script would raise if the agent loop were ever
    entered, so invocations==0 proves the short-circuit fired."""
    app = _seed_application(session, title="Software Engineer 453330")
    model = FakeToolLoopModel([])  # raises if invoked

    email = _email(
        message_id="msg-2",
        subject="Your application - Req 453330",
        body="Reference number 453330",
    )
    verdict = run_disambiguation(
        email,
        _extracted(job_title=UNSPECIFIED_JOB_TITLE),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert model.invocations == 0  # short-circuited, model never touched
    assert verdict.decision == "same_application"
    assert verdict.matched_application_id == app.id
    assert verdict.confidence == "high"


def test_requisition_id_shared_by_multiple_candidates_defers_to_agent(session):
    """When more than one candidate shares the req ID the deterministic pick is
    ambiguous, so it must fall through to the agent rather than guess one."""
    first = _seed_application(session, title="Engineer I 453330")
    second = _seed_application(session, title="Engineer II 453330")
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": second.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", second.id),
        ]
    )

    email = _email(message_id="msg-2", subject="Update on requisition 453330", body="453330")
    verdict = run_disambiguation(
        email,
        _extracted(job_title=UNSPECIFIED_JOB_TITLE),
        [first, second],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert model.invocations == 2  # short-circuit skipped, the agent ran
    assert verdict.matched_application_id == second.id


def test_no_shared_requisition_id_runs_the_agent(session):
    """A req ID present on only one side (not shared) does not short-circuit -
    the agent still runs."""
    app = _seed_application(session, title="Software Engineer 453330")
    model = FakeToolLoopModel(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id),
        ]
    )

    email = _email(message_id="msg-2", subject="Update on your application", body="no id here")
    run_disambiguation(
        email,
        _extracted(job_title=UNSPECIFIED_JOB_TITLE),
        [app],
        session=session,
        gmail_client=FakeGmailClient(_RAW_MESSAGE),
        search_client=FakeSearchClient(results=[]),
        model=model,
    )

    assert model.invocations == 2  # no shared id, agent ran normally


# --- graph routing: ambiguous case goes through the agent -------------------


def test_ambiguous_email_different_application_creates_new_row(session):
    _seed_application(session)  # Nagarro / Engineer
    model = _extract_model([_verdict_call("different_application", 0)])

    stats = _run_ambiguous(session, model)

    # Two distinct applications now exist for Nagarro on this platform.
    assert stats["applications_created"] == 1
    all_nagarro = repo.find_candidate_applications(session, company_name="Nagarro")
    assert len(all_nagarro) == 2


def test_ambiguous_email_same_application_updates_and_stores_reasoning(session):
    app = _seed_application(session)
    model = _extract_model(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id, reasoning="Same role, title just missing"),
        ]
    )

    stats = _run_ambiguous(session, model)

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1
    # The disambiguation rationale is stored on the new event's notes.
    events = [e for e in session.exec(select(StatusEvent)).all() if e.application_id == app.id]
    assert len(events) == 2
    new_event = next(e for e in events if e.source_email_id == "msg-2")
    assert new_event.notes == "Same role, title just missing"


def test_ambiguous_email_duplicate_writes_nothing(session):
    app = _seed_application(session)
    model = _extract_model(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("duplicate", app.id),
        ]
    )

    stats = _run_ambiguous(session, model)

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 0
    events = [e for e in session.exec(select(StatusEvent)).all() if e.application_id == app.id]
    assert len(events) == 1  # only the seed event
    assert repo.is_processed(session, "msg-2") is True  # idempotency intact


def test_low_confidence_same_application_routes_to_review_not_silent_merge(session):
    """M5 confidence-routed merges: a same_application verdict below the
    auto-merge bar must NOT merge into the candidate silently. Instead the email
    is written as a new application (recoverable) and a merge_into
    ReviewSuggestion is queued for a human to confirm.
    """
    app = _seed_application(session)
    model = _extract_model(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id, reasoning="probably the same", confidence="low"),
        ]
    )

    stats = _run_ambiguous(session, model)

    # New row created, candidate NOT updated with a new event.
    assert stats["applications_created"] == 1
    candidate_events = [
        e for e in session.exec(select(StatusEvent)).all() if e.application_id == app.id
    ]
    assert len(candidate_events) == 1  # only the seed event; no silent merge

    # A merge_into suggestion is queued against the candidate.
    pending = repo.list_pending_review_suggestions(session)
    assert len(pending) == 1
    suggestion = pending[0]
    assert suggestion.action == "merge_into"
    assert suggestion.application_id == app.id
    assert suggestion.confidence == "low"
    assert suggestion.message_id == "msg-2"


def test_high_confidence_same_application_auto_merges_no_suggestion(session):
    """The other side of the bar: a high-confidence merge still applies directly
    and queues nothing for review."""
    app = _seed_application(session)
    model = _extract_model(
        [
            FakeAIResponse(
                tool_calls=[{"name": "get_status_history", "args": {"application_id": app.id}, "id": "t1"}]
            ),
            _verdict_call("same_application", app.id, confidence="high"),
        ]
    )

    stats = _run_ambiguous(session, model)

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1
    assert repo.list_pending_review_suggestions(session) == []


def test_low_confidence_verdict_normalizes_unknown_confidence_to_low(session):
    """The model occasionally returns a stray confidence string; _build_verdict
    normalizes anything unrecognized to low (safest: routes to review)."""
    app = _seed_application(session)
    verdict = _build_verdict("same_application", app.id, "because", "pretty sure", {app.id: app})
    assert verdict.confidence == "low"


def test_ambiguous_email_fails_open_to_new_application_on_agent_crash(session):
    _seed_application(session)
    model = _extract_model([])  # empty script -> the agent loop raises on first invoke

    stats = _run_ambiguous(session, model)

    assert stats["applications_created"] == 1
    assert len(repo.find_candidate_applications(session, company_name="Nagarro")) == 2


def test_clear_update_never_invokes_the_agent(session):
    """An exact-title match resolves at match_existing_application and must not
    reach the agent - proven by an agent script that would blow up if played."""
    app = _seed_application(session, title="Engineer")
    model = _extract_model([], company="Nagarro", job_title="Engineer")  # exact title

    stats = _run_ambiguous(session, model)

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1
    events = [e for e in session.exec(select(StatusEvent)).all() if e.application_id == app.id]
    assert len(events) == 2  # updated, agent never touched


def test_ambiguous_second_run_processes_zero_new_emails(session):
    app = _seed_application(session)
    model = _extract_model([_verdict_call("same_application", app.id)])
    first = _run_ambiguous(session, model, message_id="msg-2")

    model2 = _extract_model([_verdict_call("same_application", app.id)])
    second = _run_ambiguous(session, model2, message_id="msg-2")

    assert first["emails_fetched"] == 1
    assert second["emails_fetched"] == 0
