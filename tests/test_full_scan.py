from datetime import date, datetime

from sqlmodel import select

from applysync.config import get_sources
from applysync.db import repository as repo
from applysync.db.models import ReviewSuggestion
from applysync.gmail.models import RawEmail
from applysync.pipeline.full_scan import process_full_scan
from applysync.pipeline.state import ClassifyAndExtractResult
from tests.fakes import FakeExtractModel, FakeStructuredModel


def _email(message_id="msg-1", sender="jobs-noreply@linkedin.com", subject="Update", body="body"):
    return RawEmail(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender=sender,
        subject=subject,
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body=body,
    )


def _model(is_relevant, company_name=None, job_title=None, status=None):
    result = ClassifyAndExtractResult(
        is_relevant=is_relevant, company_name=company_name, job_title=job_title, status=status
    )
    return FakeExtractModel(FakeStructuredModel(result=result))


def _run(session, run_id="run-1"):
    repo.create_pipeline_run(session, run_id, run_type="full_scan")
    return run_id


def test_full_scan_suggests_new_application_when_previously_irrelevant_now_relevant(session):
    repo.mark_processed(session, "msg-1", classification="irrelevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 1
    assert stats["emails_relevant"] == 1
    suggestion = session.exec(select(ReviewSuggestion)).one()
    assert suggestion.action == "new_application"
    assert suggestion.application_id is None
    assert suggestion.message_id == "msg-1"


def test_full_scan_suggests_update_existing_when_it_now_matches_an_existing_application(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.mark_processed(session, "msg-1", classification="irrelevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="interview")

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 1
    suggestion = session.exec(select(ReviewSuggestion)).one()
    assert suggestion.action == "update_existing"
    assert suggestion.application_id == application.id


def test_full_scan_suggests_update_when_re_extraction_disagrees_with_existing_application(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.add_status_event(
        session,
        application_id=application.id,
        status="applied",
        event_date=datetime(2026, 1, 1),
        source_email_id="msg-1",
    )
    repo.mark_processed(session, "msg-1", classification="relevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="rejected")

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 1
    suggestion = session.exec(select(ReviewSuggestion)).one()
    assert suggestion.action == "update_existing"
    assert suggestion.application_id == application.id
    assert suggestion.previous_extract_json is not None
    assert '"status": "applied"' in suggestion.previous_extract_json
    assert '"status": "rejected"' in suggestion.suggested_extract_json


def test_full_scan_handles_manually_declined_application_without_crashing(session):
    """Regression test for a real full-scan crash: "declined" is a
    manual-only status (set via the dashboard, never producible by the
    LLM - see CLAUDE.md), so it's deliberately excluded from
    JobApplicationEvent's status Literal. full_scan used to build the
    "previous" snapshot by constructing a JobApplicationEvent from the
    application's stored fields, which raised a pydantic ValidationError
    the moment it hit a real declined application and crashed the whole
    scan partway through.
    """
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="declined",
    )
    repo.add_status_event(
        session,
        application_id=application.id,
        status="declined",
        event_date=datetime(2026, 1, 1),
        source_email_id="msg-1",
    )
    repo.mark_processed(session, "msg-1", classification="relevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="offer")

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 1
    suggestion = session.exec(select(ReviewSuggestion)).one()
    assert suggestion.action == "update_existing"
    assert '"status": "declined"' in suggestion.previous_extract_json
    assert '"status": "offer"' in suggestion.suggested_extract_json


def test_full_scan_suggests_reclassify_irrelevant_when_no_longer_relevant(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.add_status_event(
        session,
        application_id=application.id,
        status="applied",
        event_date=datetime(2026, 1, 1),
        source_email_id="msg-1",
    )
    repo.mark_processed(session, "msg-1", classification="relevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=False)

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 1
    suggestion = session.exec(select(ReviewSuggestion)).one()
    assert suggestion.action == "reclassify_irrelevant"
    assert suggestion.application_id == application.id
    assert suggestion.suggested_extract_json is None


def test_full_scan_creates_no_suggestion_when_extraction_agrees_with_existing_application(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.add_status_event(
        session,
        application_id=application.id,
        status="applied",
        event_date=datetime(2026, 1, 1),
        source_email_id="msg-1",
    )
    repo.mark_processed(session, "msg-1", classification="relevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 0
    assert session.exec(select(ReviewSuggestion)).all() == []


def test_full_scan_creates_no_suggestion_when_still_not_relevant(session):
    repo.mark_processed(session, "msg-1", classification="irrelevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=False)

    stats = process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    assert stats["suggestions_created"] == 0
    assert session.exec(select(ReviewSuggestion)).all() == []


def test_full_scan_emails_relevant_excludes_attempted_but_irrelevant_extractions(session):
    """Regression test: emails_relevant used to just reuse emails_extracted
    (count of emails where extraction was attempted), conflating "scrutiny
    let it through" with "genuinely a real application email" - an email
    can pass scrutiny (heuristic or LLM) and still come back irrelevant from
    classify_and_extract itself.
    """
    repo.mark_processed(session, "msg-1", classification="irrelevant", pipeline_run_id="old-run")
    run_id = _run(session)
    # Narrow confirmation phrase -> heuristic scrutiny passes immediately
    # (no ambiguous-case LLM call), so is_relevant=False here reflects
    # classify_and_extract's own verdict, not the scrutiny node's.
    model = _model(is_relevant=False)

    stats = process_full_scan(
        [_email(subject="Thank you for your application at Acme")],
        model=model,
        session=session,
        sources=get_sources(),
        run_id=run_id,
    )

    assert stats["emails_relevant"] == 0
    assert stats["suggestions_created"] == 0


def test_full_scan_tracks_progress_on_pipeline_run(session):
    repo.mark_processed(session, "msg-1", classification="irrelevant", pipeline_run_id="old-run")
    run_id = _run(session)
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")

    process_full_scan([_email()], model=model, session=session, sources=get_sources(), run_id=run_id)

    run = repo.get_latest_pipeline_run(session)
    assert run.emails_total == 1
    assert run.emails_scrutinized == 1
    assert run.emails_extracted == 1
    assert run.emails_written == 1
    assert run.run_type == "full_scan"
