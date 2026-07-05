from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from langgraph.graph import END, StateGraph
from sqlmodel import Session

from applysync.config import Settings, SourcesConfig, get_settings, get_sources
from applysync.db import repository as repo
from applysync.db.init_db import get_engine, init_db
from applysync.gmail.client import GmailClient
from applysync.gmail.models import RawEmail
from applysync.gmail.query_builder import build_search_query
from applysync.llm import get_chat_model
from applysync.pipeline.nodes import (
    make_classify_node,
    make_extract_node,
    make_match_node,
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

    graph.add_node("classify_relevant", make_classify_node(model, sources))
    graph.add_node("extract_structured_data", make_extract_node(model))
    graph.add_node("match_existing_application", make_match_node(session))
    graph.add_node("upsert_db", make_upsert_node(session, run_id=run_id))
    graph.add_node(
        "mark_irrelevant", make_skip_node(session, run_id=run_id, classification="irrelevant")
    )
    graph.add_node(
        "mark_extraction_failed",
        make_skip_node(session, run_id=run_id, classification="extraction_failed"),
    )

    graph.set_entry_point("classify_relevant")

    graph.add_conditional_edges(
        "classify_relevant",
        lambda state: state["classification"],
        {"relevant": "extract_structured_data", "irrelevant": "mark_irrelevant"},
    )
    graph.add_conditional_edges(
        "extract_structured_data",
        lambda state: "ok" if state.get("extracted") is not None else "failed",
        {"ok": "match_existing_application", "failed": "mark_extraction_failed"},
    )
    graph.add_edge("match_existing_application", "upsert_db")
    graph.add_edge("upsert_db", END)
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
    """
    compiled = compile_graph(model, session, sources, run_id, checkpointer=checkpointer)

    new_emails = [e for e in emails if not repo.is_processed(session, e.message_id)]

    emails_relevant = 0
    applications_created = 0
    events_created = 0

    for email in new_emails:
        config = {"configurable": {"thread_id": email.message_id}}
        final_state = compiled.invoke({"email": email}, config=config)

        if final_state.get("classification") == "relevant":
            emails_relevant += 1

        match = final_state.get("match")
        if match is not None:
            if match.action == "new_application":
                applications_created += 1
            if match.action in ("new_application", "update_existing"):
                events_created += 1

    return {
        "emails_fetched": len(new_emails),
        "emails_relevant": emails_relevant,
        "applications_created": applications_created,
        "events_created": events_created,
    }


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
        query = build_search_query(sources)
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
