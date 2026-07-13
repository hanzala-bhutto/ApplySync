from datetime import date

from applysync.config import get_sources
from applysync.db import repository as repo
from applysync.gmail.models import RawEmail
from applysync.pipeline.nodes import (
    UNSPECIFIED_JOB_TITLE,
    make_classify_and_extract_node,
    make_match_node,
    make_scrutinize_relevance_node,
    make_skip_node,
    make_upsert_node,
)
from applysync.pipeline.state import ClassifyAndExtractResult, JobApplicationEvent, MatchDecision, RelevanceOnlyResult
from tests.fakes import FakeExtractModel, FakeStructuredModel


def _email(sender="jobs@linkedin.com", subject="Your application was sent", body="body text"):
    return RawEmail(
        message_id="msg-1",
        thread_id="thread-1",
        sender=sender,
        subject=subject,
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body=body,
    )


# --- scrutinize_relevance ---


def test_scrutinize_relevance_heuristic_pass_skips_llm_call():
    node = make_scrutinize_relevance_node(
        FakeExtractModel(FakeStructuredModel(exception=RuntimeError("should not be called"))), get_sources()
    )

    output = node({"email": _email(subject="Thank you for your application at Acme")})

    assert output["scrutiny"] == "pass"


def test_scrutinize_relevance_heuristic_reject_skips_llm_call():
    node = make_scrutinize_relevance_node(
        FakeExtractModel(FakeStructuredModel(exception=RuntimeError("should not be called"))), get_sources()
    )

    output = node({"email": _email(subject="New jobs matching your search")})

    assert output["scrutiny"] == "reject"


def test_scrutinize_relevance_ambiguous_case_calls_llm():
    result = RelevanceOnlyResult(is_relevant=True)
    node = make_scrutinize_relevance_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email(subject="Your application was sent")})

    assert output["scrutiny"] == "pass"


def test_scrutinize_relevance_ambiguous_case_llm_says_not_relevant():
    result = RelevanceOnlyResult(is_relevant=False)
    node = make_scrutinize_relevance_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email(subject="Your application was sent")})

    assert output["scrutiny"] == "reject"


def test_scrutinize_relevance_ambiguous_case_llm_failure_fails_open_to_pass():
    node = make_scrutinize_relevance_node(
        FakeExtractModel(FakeStructuredModel(exception=ValueError("boom"))), get_sources()
    )

    output = node({"email": _email(subject="Your application was sent")})

    assert output["scrutiny"] == "pass"


# --- classify_and_extract (merged) ---


def test_classify_and_extract_relevant_extracts_fields_and_guesses_platform():
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title="Engineer", status="applied"
    )
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email(sender="jobs-noreply@linkedin.com")})

    assert output["classification"] == "relevant"
    assert output["platform_hint"] == "linkedin"
    assert output["extracted"] == JobApplicationEvent(company_name="Acme", job_title="Engineer", status="applied")
    assert output["error"] is None


def test_classify_and_extract_irrelevant_skips_extraction():
    result = ClassifyAndExtractResult(is_relevant=False)
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["classification"] == "irrelevant"
    assert output["extracted"] is None


def test_classify_and_extract_missing_company_name_routes_to_error():
    result = ClassifyAndExtractResult(is_relevant=True, company_name=None, job_title="Engineer", status="applied")
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"] is None
    assert output["error"] == "missing_required_fields"


def test_classify_and_extract_missing_job_title_normalizes_instead_of_erroring():
    result = ClassifyAndExtractResult(is_relevant=True, company_name="EGYM", job_title=None, status="applied")
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["error"] is None
    assert output["extracted"].job_title == UNSPECIFIED_JOB_TITLE


def test_classify_and_extract_placeholder_job_title_text_normalizes():
    """The model is told never to invent placeholder text, but does so
    anyway in practice; normalize known placeholder strings too, not just
    None/empty.
    """
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title="Not Specified", status="applied"
    )
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"].job_title == UNSPECIFIED_JOB_TITLE


def test_classify_and_extract_status_word_job_title_normalizes():
    """Regression: an interview-appointment email that never names the role had
    job_title extracted as literally "Interview", creating a junk application
    titled "Interview". A bare status word as a title must normalize to
    unspecified so it dedupes to the real application by company instead.
    """
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Rabot Energy", job_title="Interview", status="interview"
    )
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"].job_title == UNSPECIFIED_JOB_TITLE


def test_classify_and_extract_llm_failure_routes_to_error_without_raising():
    node = make_classify_and_extract_node(
        FakeExtractModel(FakeStructuredModel(exception=ValueError("boom"))), get_sources()
    )

    output = node({"email": _email()})

    assert output["extracted"] is None
    assert "extraction_failed" in output["error"]


def test_classify_and_extract_unknown_sender_has_no_platform_hint():
    result = ClassifyAndExtractResult(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email(sender="careers@somecompany.example")})

    assert output["platform_hint"] is None


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

    application = repo.find_matching_application(session, "Acme", "Engineer")
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

    assert repo.find_matching_application(session, "Acme", "Engineer") is None
    assert repo.is_processed(session, "msg-1") is True


# --- mark_skipped ---


def test_mark_skipped_marks_processed_without_writing_application(session):
    node = make_skip_node(session, run_id="run-1", classification="irrelevant")

    node({"email": _email()})

    assert repo.is_processed(session, "msg-1") is True
    assert repo.find_matching_application(session, "Acme", "Engineer") is None
