from datetime import date

import pytest

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


def test_scrutinize_relevance_heuristic_pass_checks_body_too():
    """Regression: a real Workday confirmation ("Your job application for X
    has been successfully submitted") stated its confirmation phrase only in
    the body, not the subject, and fell through to the fallible ambiguous-
    case LLM call - which then wrongly rejected it. The heuristic pass-list
    must be checked against the body prefix too, not just the subject.
    """
    node = make_scrutinize_relevance_node(
        FakeExtractModel(FakeStructuredModel(exception=RuntimeError("should not be called"))), get_sources()
    )

    output = node(
        {
            "email": _email(
                subject="Wolters Kluwer Job Application for Senior Engineer",
                body="Hello, your job application for Senior Engineer has been successfully submitted.",
            )
        }
    )

    assert output["scrutiny"] == "pass"


def test_scrutinize_relevance_heuristic_pass_wins_over_incidental_reject_marker():
    """Regression: a real Wolters Kluwer confirmation was wrongly scrutinized
    away because its OWN footer boilerplate ("manage job alerts / create job
    alerts", generic candidate-portal navigation, nothing to do with being a
    job-alert digest) contains the substring "job alert" - one of the reject
    markers. A narrow, high-precision confirmation phrase must win over an
    incidental reject-marker match found elsewhere in the email.
    """
    node = make_scrutinize_relevance_node(
        FakeExtractModel(FakeStructuredModel(exception=RuntimeError("should not be called"))), get_sources()
    )

    output = node(
        {
            "email": _email(
                subject="Wolters Kluwer Job Application for Senior Engineer",
                body=(
                    "Your job application for Senior Engineer has been successfully submitted. "
                    "Manage job alerts: create job alerts to be notified when jobs matching your profile are posted."
                ),
            )
        }
    )

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


def test_scrutinize_relevance_ambiguous_case_uses_escalation_model_when_configured():
    """The ambiguous-case call is rare by design (the heuristic already
    screens out clear pass/reject cases), so when an escalation model is
    configured it handles this one call directly rather than the fast
    model - not a retry-after-failure, always for this specific call.
    """
    result = RelevanceOnlyResult(is_relevant=True)
    fast = FakeExtractModel(FakeStructuredModel(exception=AssertionError("should not be called")))
    escalation = FakeExtractModel(FakeStructuredModel(result=result))
    node = make_scrutinize_relevance_node(fast, get_sources(), escalation_model=escalation)

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


def test_classify_and_extract_placeholder_company_name_routes_to_error():
    """Regression: the eval harness caught the model emitting the literal
    string "unknown" as company_name instead of null - must be treated the
    same as a genuinely missing company, not stored as a real employer name.
    """
    result = ClassifyAndExtractResult(is_relevant=True, company_name="unknown", job_title="Engineer", status="applied")
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"] is None
    assert output["error"] == "missing_required_fields"


def test_classify_and_extract_talent_pool_cta_title_normalizes():
    """Regression: a KONUX confirmation email had a "Join our Talent Pool"
    button label bleed into the extracted body text right where the real
    role should be - that CTA text is not a job title.
    """
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name="KONUX", job_title="Join our Talent Pool", status="applied"
    )
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"].job_title == UNSPECIFIED_JOB_TITLE


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


@pytest.mark.parametrize(
    "bad_title",
    [
        "Technical Interview",
        "AI interview",
        "AI-powered video interview",
        "Online Assessment",
        "Phone Screening",
        "Interview",
    ],
)
def test_classify_and_extract_process_step_job_title_normalizes(bad_title):
    """Regression: the model sometimes extracts the TYPE of interview/process
    step (real examples above, seen in a real full-history resync) as the
    job_title instead of the actual role - defense-in-depth alongside the
    STEP 3 prompt guidance telling it not to.
    """
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title=bad_title, status="interview"
    )
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"].job_title == UNSPECIFIED_JOB_TITLE


@pytest.mark.parametrize(
    "real_title",
    [
        "AI Integration Engineer",
        "Backend Engineer (m/w/d) - Pricing",
        "Senior Fullstack Applied AI Engineer",
        "Call Center Software Engineer",
    ],
)
def test_classify_and_extract_real_titles_with_process_words_not_normalized(real_title):
    """The process-step regex must not false-positive on real titles that
    happen to contain a qualifier/process word (e.g. "AI", "Call") alongside
    a real role noun."""
    result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title=real_title, status="applied"
    )
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"].job_title == real_title


def test_classify_and_extract_llm_failure_routes_to_error_without_raising():
    node = make_classify_and_extract_node(
        FakeExtractModel(FakeStructuredModel(exception=ValueError("boom"))), get_sources()
    )

    output = node({"email": _email()})

    assert output["extracted"] is None
    assert "extraction_failed" in output["error"]


def test_classify_and_extract_escalates_on_fast_model_failure():
    """The rare, deliberate escalation path: the fast model's call itself
    fails, and an escalation model is configured - it gets one retry with
    the same prompt rather than immediately giving up.
    """
    fast = FakeExtractModel(FakeStructuredModel(exception=ValueError("boom")))
    escalation_result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title="Engineer", status="applied"
    )
    escalation = FakeExtractModel(FakeStructuredModel(result=escalation_result))
    node = make_classify_and_extract_node(fast, get_sources(), escalation_model=escalation)

    output = node({"email": _email()})

    assert output["error"] is None
    assert output["extracted"].company_name == "Acme"


def test_classify_and_extract_escalates_on_missing_company_name():
    """The other concrete escalation trigger: the fast model returned a
    relevant result but no usable company_name - escalate rather than
    immediately routing to missing_required_fields.
    """
    fast_result = ClassifyAndExtractResult(is_relevant=True, company_name=None, job_title="Engineer", status="applied")
    fast = FakeExtractModel(FakeStructuredModel(result=fast_result))
    escalation_result = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title="Engineer", status="applied"
    )
    escalation = FakeExtractModel(FakeStructuredModel(result=escalation_result))
    node = make_classify_and_extract_node(fast, get_sources(), escalation_model=escalation)

    output = node({"email": _email()})

    assert output["error"] is None
    assert output["extracted"].company_name == "Acme"


def test_classify_and_extract_no_escalation_model_configured_uses_fast_model_result():
    """Backward-compat / default behavior: no escalation_model means the
    fast model's result is used as-is, even on a failure signal - matches
    behavior before escalation existed.
    """
    result = ClassifyAndExtractResult(is_relevant=True, company_name=None, job_title="Engineer", status="applied")
    node = make_classify_and_extract_node(FakeExtractModel(FakeStructuredModel(result=result)), get_sources())

    output = node({"email": _email()})

    assert output["extracted"] is None
    assert output["error"] == "missing_required_fields"


def test_classify_and_extract_escalation_also_fails_uses_original_failure_path():
    fast = FakeExtractModel(FakeStructuredModel(exception=ValueError("boom")))
    escalation = FakeExtractModel(FakeStructuredModel(exception=ValueError("also boom")))
    node = make_classify_and_extract_node(fast, get_sources(), escalation_model=escalation)

    output = node({"email": _email()})

    assert output["extracted"] is None
    assert "extraction_failed" in output["error"]


def test_classify_and_extract_good_fast_result_never_escalates():
    """The common case: a good result from the fast model must not trigger
    an unnecessary (rate-limited, slower) escalation call."""
    fast_result = ClassifyAndExtractResult(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    fast = FakeExtractModel(FakeStructuredModel(result=fast_result))
    escalation = FakeExtractModel(FakeStructuredModel(exception=AssertionError("should not be called")))
    node = make_classify_and_extract_node(fast, get_sources(), escalation_model=escalation)

    output = node({"email": _email()})

    assert output["extracted"].company_name == "Acme"


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


def test_match_existing_application_ambiguous_when_company_is_fuzzy_only(session):
    """A fuzzy-only company hit (typo) must NOT auto-resolve to
    update_existing even though the title matches exactly - it has to route
    to the disambiguation agent instead (see make_match_node's routing in
    graph.py), since the company itself isn't confirmed to be the same one.
    """
    application = repo.create_application(
        session,
        company_name="EGYM",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    node = make_match_node(session)
    extracted = JobApplicationEvent(company_name="EGYG", job_title="Engineer", status="interview")

    result = node({"extracted": extracted, "platform_hint": "linkedin"})

    assert result["match"] is None
    assert result["candidate_ids"] == [application.id]


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
