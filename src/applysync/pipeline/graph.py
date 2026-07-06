from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.graph import END, StateGraph
from sqlmodel import Session

from applysync.config import Settings, SourcesConfig, get_settings, get_sources
from applysync.db import repository as repo
from applysync.db.init_db import get_engine, init_db
from applysync.db.models import Application
from applysync.gmail.client import GmailClient
from applysync.gmail.models import RawEmail
from applysync.gmail.query_builder import build_search_query
from applysync.llm import get_chat_model
from applysync.pipeline.nodes import (
    make_classify_and_extract_node,
    make_match_node,
    make_scrutinize_relevance_node,
    make_skip_node,
    make_upsert_node,
)
from applysync.pipeline.state import EmailState


def build_graph(model, session: Session, sources: SourcesConfig, run_id: str) -> StateGraph:
    """One EmailState flows through this graph per invocation (one email per
    graph.invoke call, driven by the loop in process_emails). fetch_emails
    itself is not a graph node: it is a plain batch fetch in process_emails/
    run_sync, since LangGraph's per-node execution here operates on a single
    email at a time. See CLAUDE.md for the fuller rationale.
    """
    graph = StateGraph(EmailState)

    graph.add_node("scrutinize_relevance", make_scrutinize_relevance_node(model, sources))
    graph.add_node("classify_and_extract", make_classify_and_extract_node(model, sources))
    graph.add_node("match_existing_application", make_match_node(session))
    graph.add_node("upsert_db", make_upsert_node(session, run_id=run_id))
    graph.add_node(
        "mark_scrutiny_rejected", make_skip_node(session, run_id=run_id, classification="scrutiny_rejected")
    )
    graph.add_node(
        "mark_irrelevant", make_skip_node(session, run_id=run_id, classification="irrelevant")
    )
    graph.add_node(
        "mark_extraction_failed",
        make_skip_node(session, run_id=run_id, classification="extraction_failed"),
    )

    graph.set_entry_point("scrutinize_relevance")

    graph.add_conditional_edges(
        "scrutinize_relevance",
        lambda state: state.get("scrutiny"),
        {
            "pass": "classify_and_extract",
            "reject": "mark_scrutiny_rejected",
        },
    )

    def _route(state):
        if state.get("extracted") is not None:
            return "ok"
        if state.get("classification") == "irrelevant":
            return "irrelevant"
        return "failed"

    graph.add_conditional_edges(
        "classify_and_extract",
        _route,
        {
            "ok": "match_existing_application",
            "irrelevant": "mark_irrelevant",
            "failed": "mark_extraction_failed",
        },
    )
    graph.add_edge("match_existing_application", "upsert_db")
    graph.add_edge("upsert_db", END)
    graph.add_edge("mark_scrutiny_rejected", END)
    graph.add_edge("mark_irrelevant", END)
    graph.add_edge("mark_extraction_failed", END)

    return graph


def compile_graph(model, session: Session, sources: SourcesConfig, run_id: str, checkpointer=None):
    return build_graph(model, session, sources, run_id).compile(checkpointer=checkpointer)


def make_checkpointer(db_path: Path):
    """Crash-recovery only, not the idempotency mechanism: skipping
    already-processed emails (processed_emails table, checked in
    process_emails below) is what prevents duplicate work on every scheduled
    run; this just lets a mid-graph crash resume instead of restart.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


# Nodes that end an email's path through the graph (mutually exclusive per
# email: exactly one of these runs for any given email), used to count
# emails_written incrementally as the run streams rather than only at the end.
_TERMINAL_NODES = {"upsert_db", "mark_scrutiny_rejected", "mark_irrelevant", "mark_extraction_failed"}

# Cheap SQLite commits locally; this is a safety margin against pathological
# burst cases (e.g. many emails resolved in the same instant), not a hard
# requirement for correctness.
_PROGRESS_FLUSH_INTERVAL_SECONDS = 0.5


def process_emails(
    emails: list[RawEmail],
    *,
    model,
    session: Session,
    sources: SourcesConfig,
    run_id: str,
    checkpointer=None,
) -> dict:
    """Core, unit-testable pipeline logic: filters out already-processed
    emails, runs each new one through the graph, and tallies stats. Takes
    the email list directly rather than fetching it, so tests can pass
    fixtures instead of hitting the real Gmail API.

    Uses compiled.stream(stream_mode="updates") rather than .invoke() so each
    node's completion is observable as it happens (see
    repo.update_pipeline_run_progress) - the same per-email final result is
    reconstructed by merging each node's partial update, since there is no
    custom state reducer here (later updates simply overwrite the same keys,
    matching what .invoke() returned before this change).
    """
    compiled = compile_graph(model, session, sources, run_id, checkpointer=checkpointer)

    new_emails = [e for e in emails if not repo.is_processed(session, e.message_id)]

    emails_relevant = 0
    applications_created = 0
    events_created = 0
    emails_scrutinized = 0
    emails_extracted = 0
    emails_written = 0

    repo.update_pipeline_run_progress(session, run_id, emails_total=len(new_emails))
    last_flush = time.monotonic()

    def _flush_progress(*, force: bool = False) -> None:
        nonlocal last_flush
        now = time.monotonic()
        if not force and now - last_flush < _PROGRESS_FLUSH_INTERVAL_SECONDS:
            return
        repo.update_pipeline_run_progress(
            session,
            run_id,
            emails_scrutinized=emails_scrutinized,
            emails_extracted=emails_extracted,
            emails_written=emails_written,
        )
        last_flush = now

    for email in new_emails:
        config = {"configurable": {"thread_id": email.message_id}}
        final_state: dict = {}

        for update in compiled.stream({"email": email}, config=config, stream_mode="updates"):
            for node_name, node_output in update.items():
                final_state.update(node_output or {})
                if node_name == "scrutinize_relevance":
                    emails_scrutinized += 1
                elif node_name == "classify_and_extract":
                    emails_extracted += 1
                if node_name in _TERMINAL_NODES:
                    emails_written += 1
            _flush_progress()

        if final_state.get("classification") == "relevant":
            emails_relevant += 1

        match = final_state.get("match")
        if match is not None:
            if match.action == "new_application":
                applications_created += 1
            if match.action in ("new_application", "update_existing"):
                events_created += 1

    _flush_progress(force=True)

    return {
        "emails_fetched": len(new_emails),
        "emails_relevant": emails_relevant,
        "applications_created": applications_created,
        "events_created": events_created,
    }


# Extra lookback beyond the last run's own date, applied on top of the
# same-day overlap `last_successful_run_started_at` already gives (Gmail's
# after: operator is date-, not time-, granularity). Found necessary for
# real: two manual test syncs completed (0 emails found, but still
# "successful") just after midnight, which advanced the bookmark to that new
# calendar day - permanently excluding a real email from just before
# midnight the day before that a confirmation_keywords gap had also caused
# to be missed. processed_emails already dedupes anything re-fetched in this
# wider window, so the buffer costs a slightly larger Gmail query, not
# reprocessing.
SYNC_LOOKBACK_BUFFER_DAYS = 3


def run_sync(settings: Settings | None = None) -> dict:
    """Real end-to-end entrypoint (applysync sync): fetches from the actual
    Gmail API and an actual LLM, unlike process_emails which is exercised
    directly in tests.
    """
    settings = settings or get_settings()
    sources = get_sources()

    init_db(settings.db_path)
    with Session(get_engine(settings.db_path)) as session:
        run_id = str(uuid.uuid4())
        repo.create_pipeline_run(session, run_id)

        model = get_chat_model(settings)
        client = GmailClient(settings)
        last_run_started_at = repo.last_successful_run_started_at(session)
        after_date = (
            last_run_started_at.date() - timedelta(days=SYNC_LOOKBACK_BUFFER_DAYS)
            if last_run_started_at
            else None
        )
        query = build_search_query(sources, after=after_date)
        emails = client.fetch_messages(query)

        checkpointer = make_checkpointer(settings.db_path)
        stats = process_emails(
            emails,
            model=model,
            session=session,
            sources=sources,
            run_id=run_id,
            checkpointer=checkpointer,
        )

        repo.finish_pipeline_run(session, run_id, **stats)
        return {"run_id": run_id, **stats}


def reprocess_application(
    session: Session,
    application_id: int,
    *,
    gmail_client: GmailClient,
    model,
) -> Application | None:
    """Dashboard "reprocess" action: refetches the email behind the most
    recent status event and re-runs extraction only (not the full graph -
    match_existing_application isn't relevant here, we already know which
    application this is), then updates its fields/status in place.

    Returns None if there's nothing to reprocess: unknown application, no
    events yet, or the most recent event was itself a manual correction
    (source_email_id is None, so there's no email to refetch).
    """
    application = repo.get_application(session, application_id)
    if application is None:
        return None

    timeline = repo.application_timeline(session, application_id)
    if not timeline or timeline[-1].source_email_id is None:
        return application

    message_id = timeline[-1].source_email_id
    email = gmail_client.get_message(message_id)

    classify_and_extract = make_classify_and_extract_node(model, get_sources())
    result = classify_and_extract({"email": email})
    extracted = result.get("extracted")
    if extracted is None:
        return application

    repo.update_application_fields(
        session, application_id, company_name=extracted.company_name, job_title=extracted.job_title
    )
    if extracted.status != application.current_status:
        repo.add_status_event(
            session,
            application_id=application_id,
            status=extracted.status,
            event_date=datetime.now(timezone.utc).replace(tzinfo=None),
            source_email_id=message_id,
            raw_extract_json=extracted.model_dump_json(),
            notes="Reprocessed from the dashboard",
        )

    session.refresh(application)
    return application
