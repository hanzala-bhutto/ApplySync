from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import select

from applysync.config import get_sources
from applysync.db import repository as repo
from applysync.db.models import ProcessedEmail, StatusEvent
from applysync.gmail.models import RawEmail
from applysync.pipeline.graph import process_emails
from applysync.pipeline.state import ClassifyAndExtractResult
from tests.fakes import FakeExtractModel, FakeStructuredModel


def _email(message_id="msg-1", sender="jobs-noreply@linkedin.com", subject="Your application was sent", body="body"):
    return RawEmail(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender=sender,
        subject=subject,
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body=body,
    )


def _model(is_relevant, company_name=None, job_title=None, status=None, exception=None):
    if exception is not None:
        return FakeExtractModel(FakeStructuredModel(exception=exception))
    result = ClassifyAndExtractResult(
        is_relevant=is_relevant, company_name=company_name, job_title=job_title, status=status
    )
    return FakeExtractModel(FakeStructuredModel(result=result))


def test_relevant_email_creates_application_end_to_end(session):
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")

    stats = process_emails(
        [_email()],
        model=model,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    assert stats["emails_fetched"] == 1
    assert stats["emails_relevant"] == 1
    assert stats["applications_created"] == 1
    assert stats["events_created"] == 1
    assert repo.find_matching_application(session, "Acme", "Engineer", "linkedin") is not None


def test_irrelevant_email_is_marked_processed_without_creating_application(session):
    model = _model(is_relevant=False)

    stats = process_emails(
        [_email()],
        model=model,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert repo.is_processed(session, "msg-1") is True


def test_second_run_over_same_batch_processes_zero_new_emails(session):
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    emails = [_email()]

    first = process_emails(
        emails, model=model, session=session, sources=get_sources(), run_id="run-1", checkpointer=MemorySaver()
    )
    second = process_emails(
        emails, model=model, session=session, sources=get_sources(), run_id="run-2", checkpointer=MemorySaver()
    )

    assert first["emails_fetched"] == 1
    assert first["applications_created"] == 1
    assert second["emails_fetched"] == 0
    assert second["applications_created"] == 0

    application = repo.find_matching_application(session, "Acme", "Engineer", "linkedin")
    events = [e for e in session.exec(select(StatusEvent)).all() if e.application_id == application.id]
    assert len(events) == 1


def test_status_update_email_links_to_existing_application_not_a_duplicate(session):
    model_applied = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    process_emails(
        [_email(message_id="msg-1")],
        model=model_applied,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_interview = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="interview")
    stats = process_emails(
        [_email(message_id="msg-2", subject="Update on your application")],
        model=model_interview,
        session=session,
        sources=get_sources(),
        run_id="run-2",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1

    application = repo.find_matching_application(session, "Acme", "Engineer", "linkedin")
    assert application.current_status == "interview"


def test_repeat_confirmation_emails_without_job_title_dedupe_to_one_application(session):
    """Regression test for a real bug found running against a live inbox:
    two near-duplicate EGYM confirmation emails, neither mentioning a job
    title, produced two application rows because the model filled job_title
    with inconsistent placeholder text ("Not specified" vs "Unknown"). Both
    should now normalize to the same value and dedupe to one application with
    two status events.
    """
    model_first = _model(is_relevant=True, company_name="EGYM", job_title=None, status="applied")
    process_emails(
        [_email(message_id="msg-1", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_first,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_second = _model(is_relevant=True, company_name="EGYM", job_title=None, status="applied")
    stats = process_emails(
        [_email(message_id="msg-2", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_second,
        session=session,
        sources=get_sources(),
        run_id="run-2",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1

    events = [
        e
        for e in session.exec(select(StatusEvent)).all()
        if e.source_email_id in ("msg-1", "msg-2")
    ]
    assert len(events) == 2
    assert len({e.application_id for e in events}) == 1


def test_scrutiny_rejected_email_never_reaches_classify_and_extract(session):
    """A digest-marker subject should short-circuit at scrutinize_relevance
    without ever invoking classify_and_extract - proven here by using a model
    that raises if invoked at all.
    """
    model = FakeExtractModel(FakeStructuredModel(exception=RuntimeError("classify_and_extract must not run")))

    stats = process_emails(
        [_email(subject="New jobs matching your search - jobs for you this week")],
        model=model,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    processed = session.exec(select(ProcessedEmail).where(ProcessedEmail.message_id == "msg-1")).one()
    assert processed.classification == "scrutiny_rejected"


def test_repeat_confirmation_emails_with_differing_legal_suffix_dedupe_to_one_application(session):
    """Regression test for the actual real-inbox finding: the same EGYM
    application's two confirmation emails extracted as company_name "EGYM"
    and "EGYM SE" respectively, which used to create two application rows.
    """
    model_first = _model(is_relevant=True, company_name="EGYM", job_title=None, status="applied")
    process_emails(
        [_email(message_id="msg-1", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_first,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_second = _model(is_relevant=True, company_name="EGYM SE", job_title=None, status="applied")
    stats = process_emails(
        [_email(message_id="msg-2", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_second,
        session=session,
        sources=get_sources(),
        run_id="run-2",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1
