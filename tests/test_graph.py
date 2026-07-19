from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import select

from applysync.config import get_sources
from applysync.db import repository as repo
from applysync.db.models import Application, PipelineRun, ProcessedEmail, StatusEvent
from applysync.gmail.models import RawEmail
from applysync.pipeline import graph as graph_module
from applysync.pipeline.graph import process_emails
from applysync.pipeline.state import ClassifyAndExtractResult
from applysync.run_control import clear_cancel, request_cancel
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


def _process_emails(emails, *, model, session, run_id, sources=None, checkpointer=None):
    """process_emails now writes incremental progress to the pipeline_run row
    (see repo.update_pipeline_run_progress), which real production code
    always creates first via repo.create_pipeline_run (run_sync). Tests call
    process_emails directly, so this wrapper creates that row first.
    """
    repo.create_pipeline_run(session, run_id)
    return process_emails(
        emails,
        model=model,
        session=session,
        sources=sources or get_sources(),
        run_id=run_id,
        checkpointer=checkpointer or MemorySaver(),
    )


def test_relevant_email_creates_application_end_to_end(session):
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")

    stats = _process_emails(
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
    assert repo.find_matching_application(session, "Acme", "Engineer") is not None


def test_irrelevant_email_is_marked_processed_without_creating_application(session):
    model = _model(is_relevant=False)

    stats = _process_emails(
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

    first = _process_emails(
        emails, model=model, session=session, sources=get_sources(), run_id="run-1", checkpointer=MemorySaver()
    )
    second = _process_emails(
        emails, model=model, session=session, sources=get_sources(), run_id="run-2", checkpointer=MemorySaver()
    )

    assert first["emails_fetched"] == 1
    assert first["applications_created"] == 1
    assert second["emails_fetched"] == 0
    assert second["applications_created"] == 0

    application = repo.find_matching_application(session, "Acme", "Engineer")
    events = [e for e in session.exec(select(StatusEvent)).all() if e.application_id == application.id]
    assert len(events) == 1


def test_status_update_email_links_to_existing_application_not_a_duplicate(session):
    model_applied = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    _process_emails(
        [_email(message_id="msg-1")],
        model=model_applied,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_interview = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="interview")
    stats = _process_emails(
        [_email(message_id="msg-2", subject="Update on your application")],
        model=model_interview,
        session=session,
        sources=get_sources(),
        run_id="run-2",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1

    application = repo.find_matching_application(session, "Acme", "Engineer")
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
    _process_emails(
        [_email(message_id="msg-1", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_first,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_second = _model(is_relevant=True, company_name="EGYM", job_title=None, status="applied")
    stats = _process_emails(
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


def test_process_emails_tracks_incremental_progress_counters(session):
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")

    _process_emails([_email()], model=model, session=session, run_id="run-1")

    run = session.get(PipelineRun, "run-1")
    assert run.emails_total == 1
    assert run.emails_scrutinized == 1
    assert run.emails_extracted == 1
    assert run.emails_written == 1


def test_langfuse_handler_groups_emails_under_the_run_id_session(session, monkeypatch):
    """When a langfuse_handler is passed, every email's stream config should
    carry the callback plus a langfuse_session_id matching this run's run_id -
    that's what groups a whole sync into one browsable Langfuse session
    instead of only individually filterable per-email traces."""
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    seen_configs = []

    real_compile_graph = graph_module.compile_graph

    def _spying_compile_graph(*args, **kwargs):
        compiled = real_compile_graph(*args, **kwargs)
        real_stream = compiled.stream

        def _spying_stream(input_, config=None, **stream_kwargs):
            seen_configs.append(config)
            return real_stream(input_, config=config, **stream_kwargs)

        compiled.stream = _spying_stream
        return compiled

    monkeypatch.setattr(graph_module, "compile_graph", _spying_compile_graph)

    fake_handler = BaseCallbackHandler()
    repo.create_pipeline_run(session, "run-lf")
    process_emails(
        [_email()],
        model=model,
        session=session,
        sources=get_sources(),
        run_id="run-lf",
        checkpointer=MemorySaver(),
        langfuse_handler=fake_handler,
    )

    assert len(seen_configs) == 1
    assert seen_configs[0]["callbacks"] == [fake_handler]
    assert seen_configs[0]["metadata"] == {"langfuse_session_id": "run-lf"}


def test_scrutiny_rejected_email_never_reaches_classify_and_extract(session):
    """A digest-marker subject should short-circuit at scrutinize_relevance
    without ever invoking classify_and_extract - proven here by using a model
    that raises if invoked at all.
    """
    model = FakeExtractModel(FakeStructuredModel(exception=RuntimeError("classify_and_extract must not run")))

    stats = _process_emails(
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


def test_same_application_across_different_platforms_updates_not_duplicates(session):
    """Platform is attribution, not identity: a confirmation via one sender and
    a later status update via a different sender (a different guessed platform)
    for the same company+title must update the one application, not fork a new
    row. Regression for the real Galvany fragmentation - interviews tracked as
    platform "other", the rejection arriving via ashbyhq.com as platform
    "ashby", landing in separate rows so the watched card never flipped.
    """
    model_applied = _model(
        is_relevant=True, company_name="Galvany Energy", job_title="Backend Engineer", status="applied"
    )
    _process_emails(
        [_email(message_id="msg-1", sender="jobs@galvany.example", subject="Your application for Backend Engineer")],
        model=model_applied,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_rejected = _model(
        is_relevant=True, company_name="Galvany Energy", job_title="Backend Engineer", status="rejected"
    )
    stats = _process_emails(
        [_email(message_id="msg-2", sender="no-reply@ashbyhq.com", subject="Update on your application")],
        model=model_rejected,
        session=session,
        sources=get_sources(),
        run_id="run-2",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1
    galvany = [a for a in session.exec(select(Application)).all() if "galvany" in a.company_name.lower()]
    assert len(galvany) == 1
    assert galvany[0].current_status == "rejected"


def test_repeat_confirmation_emails_with_differing_legal_suffix_dedupe_to_one_application(session):
    """Regression test for the actual real-inbox finding: the same EGYM
    application's two confirmation emails extracted as company_name "EGYM"
    and "EGYM SE" respectively, which used to create two application rows.
    """
    model_first = _model(is_relevant=True, company_name="EGYM", job_title=None, status="applied")
    _process_emails(
        [_email(message_id="msg-1", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_first,
        session=session,
        sources=get_sources(),
        run_id="run-1",
        checkpointer=MemorySaver(),
    )

    model_second = _model(is_relevant=True, company_name="EGYM SE", job_title=None, status="applied")
    stats = _process_emails(
        [_email(message_id="msg-2", sender="jobs@egym.com", subject="Thank you for your application at EGYM!")],
        model=model_second,
        session=session,
        sources=get_sources(),
        run_id="run-2",
        checkpointer=MemorySaver(),
    )

    assert stats["applications_created"] == 0
    assert stats["events_created"] == 1


class _CancelDuringFirstCall:
    """FakeStructuredModel-like stub whose first .invoke() call requests
    cancellation as a side effect (simulating a stop request arriving while
    the first email is mid-flight), so the test can verify the SECOND email
    in the batch never gets processed, not just that the flag is checked."""

    def __init__(self, result):
        self._result = result
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            request_cancel()
        return self._result

    def with_retry(self, **kwargs):
        return self


def test_process_emails_stops_before_the_next_email_once_cancel_is_requested(session):
    """A stop request arriving mid-first-email must let that email finish
    (checkpointing/idempotency assumes a graph run either completes or never
    started - see run_control.py), then stop before starting the next one."""
    clear_cancel()
    try:
        result = ClassifyAndExtractResult(
            is_relevant=True, company_name="Acme", job_title="Engineer", status="applied"
        )
        model = FakeExtractModel(_CancelDuringFirstCall(result))

        emails = [
            _email(message_id="msg-1", subject="Thank you for your application"),
            _email(message_id="msg-2", subject="Thank you for your application"),
        ]
        stats = _process_emails(emails, model=model, session=session, run_id="run-1")

        assert stats["cancelled"] is True
        assert stats["applications_created"] == 1
        assert repo.is_processed(session, "msg-1")
        assert not repo.is_processed(session, "msg-2")
    finally:
        clear_cancel()


def test_process_emails_processes_nothing_when_already_cancelled_before_starting(session):
    clear_cancel()
    try:
        request_cancel()
        model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
        stats = _process_emails(
            [_email(message_id="msg-1", subject="Thank you for your application")],
            model=model,
            session=session,
            run_id="run-1",
        )

        assert stats["cancelled"] is True
        assert stats["applications_created"] == 0
        assert not repo.is_processed(session, "msg-1")
    finally:
        clear_cancel()


def test_process_emails_not_cancelled_reports_cancelled_false(session):
    model = _model(is_relevant=True, company_name="Acme", job_title="Engineer", status="applied")
    stats = _process_emails(
        [_email(message_id="msg-1", subject="Thank you for your application")],
        model=model,
        session=session,
        run_id="run-1",
    )
    assert stats["cancelled"] is False
