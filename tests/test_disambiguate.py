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
from applysync.pipeline.state import ClassifyAndExtractResult, JobApplicationEvent
from applysync.research.disambiguate import (
    MAX_AGENT_TURNS,
    DisambiguationError,
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


def _verdict_call(decision, matched_application_id, reasoning="because", call_id="c1"):
    return FakeAIResponse(
        tool_calls=[
            {
                "name": "submit_verdict",
                "args": {
                    "decision": decision,
                    "matched_application_id": matched_application_id,
                    "reasoning": reasoning,
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


def test_agent_submits_same_application_verdict(session):
    app = _seed_application(session)
    model = FakeToolLoopModel([_verdict_call("same_application", app.id)])

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


def test_agent_hallucinated_id_raises(session):
    app = _seed_application(session)
    model = FakeToolLoopModel([_verdict_call("same_application", 999)])

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
    model = _extract_model([_verdict_call("same_application", app.id, reasoning="Same role, title just missing")])

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
    model = _extract_model([_verdict_call("duplicate", app.id)])

    stats = _run_ambiguous(session, model)

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 0
    events = [e for e in session.exec(select(StatusEvent)).all() if e.application_id == app.id]
    assert len(events) == 1  # only the seed event
    assert repo.is_processed(session, "msg-2") is True  # idempotency intact


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
