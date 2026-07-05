from datetime import date, datetime

import pytest

from applysync.config import get_sources
from applysync.db import repository as repo
from applysync.gmail.models import RawEmail
from applysync.pipeline.nodes import (
    make_classify_node,
    make_extract_node,
    make_match_node,
    make_skip_node,
    make_upsert_node,
)
from applysync.pipeline.state import JobApplicationEvent, MatchDecision


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeChatModel:
    """Mocks the LangChain model boundary for classify_relevant: .invoke
    returns an object with .content, same shape as a real ChatNVIDIA response.
    """

    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return FakeResponse(self._content)


class FakeStructuredModel:
    def __init__(self, result=None, exception=None):
        self._result = result
        self._exception = exception

    def invoke(self, messages):
        if self._exception is not None:
            raise self._exception
        return self._result


class FakeExtractModel:
    """Mocks .with_structured_output(...).invoke(...) for extract_structured_data."""

    def __init__(self, structured_model: FakeStructuredModel):
        self._structured_model = structured_model

    def with_structured_output(self, schema):
        return self._structured_model


def _email(sender="jobs@linkedin.com", subject="Your application was sent", body="body text"):
    return RawEmail(
        message_id="msg-1",
        thread_id="thread-1",
        sender=sender,
        subject=subject,
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body=body,
    )


# --- classify_relevant ---


def test_classify_relevant_marks_relevant_and_guesses_platform():
    node = make_classify_node(FakeChatModel("RELEVANT"), get_sources())
    result = node({"email": _email(sender="jobs-noreply@linkedin.com")})
    assert result["classification"] == "relevant"
    assert result["platform_hint"] == "linkedin"


def test_classify_relevant_marks_irrelevant():
    node = make_classify_node(FakeChatModel("IRRELEVANT"), get_sources())
    result = node({"email": _email()})
    assert result["classification"] == "irrelevant"


def test_classify_relevant_unknown_sender_has_no_platform_hint():
    node = make_classify_node(FakeChatModel("RELEVANT"), get_sources())
    result = node({"email": _email(sender="careers@somecompany.example")})
    assert result["platform_hint"] is None


# --- extract_structured_data ---


def test_extract_structured_data_success():
    event = JobApplicationEvent(company_name="Acme", job_title="Engineer", status="applied")
    node = make_extract_node(FakeExtractModel(FakeStructuredModel(result=event)))

    result = node({"email": _email(), "platform_hint": "linkedin"})

    assert result["extracted"] == event
    assert result["error"] is None


def test_extract_structured_data_missing_required_fields_routes_to_error():
    event = JobApplicationEvent(company_name="", job_title="Engineer", status="applied")
    node = make_extract_node(FakeExtractModel(FakeStructuredModel(result=event)))

    result = node({"email": _email(), "platform_hint": "linkedin"})

    assert result["extracted"] is None
    assert result["error"] == "missing_required_fields"


def test_extract_structured_data_llm_failure_routes_to_error_without_raising():
    node = make_extract_node(FakeExtractModel(FakeStructuredModel(exception=ValueError("boom"))))

    result = node({"email": _email(), "platform_hint": "linkedin"})

    assert result["extracted"] is None
    assert "extraction_failed" in result["error"]


# --- match_existing_application ---


def test_match_existing_application_new_when_no_match(session):
    node = make_match_node(session)
    extracted = JobApplicationEvent(company_name="Acme", job_title="Engineer", status="applied")

    result = node({"extracted": extracted, "platform_hint": "linkedin"})

    assert result["match"].action == "new_application"


def test_match_existing_application_updates_when_match_found(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    node = make_match_node(session)
    extracted = JobApplicationEvent(company_name="Acme", job_title="Engineer", status="interview")

    result = node({"extracted": extracted, "platform_hint": "linkedin"})

    assert result["match"].action == "update_existing"
    assert result["match"].application_id == application.id


# --- upsert_db ---


def test_upsert_db_creates_new_application_and_marks_processed(session):
    node = make_upsert_node(session, run_id="run-1")
    extracted = JobApplicationEvent(company_name="Acme", job_title="Engineer", status="applied")
    state = {
        "email": _email(),
        "extracted": extracted,
        "match": MatchDecision(action="new_application"),
        "platform_hint": "linkedin",
    }

    node(state)

    application = repo.find_matching_application(session, "Acme", "Engineer", "linkedin")
    assert application is not None
    assert application.current_status == "applied"
    assert repo.is_processed(session, "msg-1") is True


def test_upsert_db_updates_existing_application_status(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    node = make_upsert_node(session, run_id="run-1")
    extracted = JobApplicationEvent(company_name="Acme", job_title="Engineer", status="interview")
    state = {
        "email": _email(),
        "extracted": extracted,
        "match": MatchDecision(action="update_existing", application_id=application.id),
        "platform_hint": "linkedin",
    }

    node(state)

    session.refresh(application)
    assert application.current_status == "interview"
    assert repo.is_processed(session, "msg-1") is True


def test_upsert_db_duplicate_skip_writes_nothing_but_marks_processed(session):
    node = make_upsert_node(session, run_id="run-1")
    extracted = JobApplicationEvent(company_name="Acme", job_title="Engineer", status="applied")
    state = {
        "email": _email(),
        "extracted": extracted,
        "match": MatchDecision(action="duplicate_skip"),
        "platform_hint": "linkedin",
    }

    node(state)

    assert repo.find_matching_application(session, "Acme", "Engineer", "linkedin") is None
    assert repo.is_processed(session, "msg-1") is True


# --- mark_skipped ---


def test_mark_skipped_marks_processed_without_writing_application(session):
    node = make_skip_node(session, run_id="run-1", classification="irrelevant")

    node({"email": _email()})

    assert repo.is_processed(session, "msg-1") is True
    assert repo.find_matching_application(session, "Acme", "Engineer", "linkedin") is None
