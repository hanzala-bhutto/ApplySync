from __future__ import annotations

import logging
import queue
import threading

from applysync.config import Settings

logger = logging.getLogger(__name__)

# In-process pub/sub for the /sync page's live pipeline-graph animation
# (see docs/feasibility/pipeline-flow-visualization.md). Same "diagnostic,
# never load-bearing" posture as Langfuse tracing above: process_emails/
# process_full_audit call publish_node_event() unconditionally from their
# own node-execution loop, and it is a near-free no-op whenever nobody is
# subscribed via GET /api/sync/stream - the pipeline must behave identically
# whether or not the Sync page happens to be open in a browser tab.
#
# Plain queue.Queue (thread-safe stdlib), not asyncio: sync/full-audit runs
# execute in a background threading.Thread (see web/sync.py), not the async
# event loop, so publishing has to work from a plain thread. The SSE route
# bridges back to async via a threadpool read (see web/sync.py).
_lock = threading.Lock()
_subscribers: list[queue.Queue] = []


def publish_node_event(node: str, message_id: str) -> None:
    if not _subscribers:
        return
    event = {"node": node, "message_id": message_id}
    with _lock:
        subscribers = list(_subscribers)
    for q in subscribers:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass  # a slow/stuck subscriber must never block the pipeline


def subscribe_to_node_events() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=500)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe_from_node_events(q: queue.Queue) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def get_langfuse_handler(settings: Settings):
    """Builds a Langfuse LangChain callback handler for tracing a sync's
    LangGraph run (every node, LLM call, and the disambiguation agent's tool
    loop - callbacks propagate automatically to nested `.invoke()` calls made
    within a node's own execution, no per-node wiring needed) or a standalone
    agent call like company research.

    Returns None whenever tracing isn't configured (settings.langfuse_public_key/
    secret_key unset - a fresh checkout before the user has run `docker compose
    up -d` in langfuse/, or unit tests) or the client fails to initialize
    (e.g. the Langfuse stack isn't running). The pipeline must behave
    identically either way - tracing is diagnostic, never load-bearing -
    matching the fail-open posture of scrutinize_relevance and the
    disambiguation agent.
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        return CallbackHandler()
    except Exception as exc:  # noqa: BLE001 - tracing must never block a sync
        logger.warning("Langfuse init failed, tracing disabled: %s", exc)
        return None
