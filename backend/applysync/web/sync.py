from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from applysync.config import Settings
from applysync.db import repository as repo
from applysync.db.models import PipelineRun
from applysync.observability import subscribe_to_node_events, unsubscribe_from_node_events
from applysync.pipeline.full_audit import full_audit as _default_full_audit
from applysync.pipeline.graph import run_sync as _default_run_sync
from applysync.run_control import clear_cancel, request_cancel

# How long the SSE read blocks before sending a keep-alive comment - long
# enough to be cheap when idle, short enough that a proxy/browser doesn't
# time out the connection during a real multi-minute sync.
_STREAM_KEEPALIVE_SECONDS = 15.0

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])

# Single-process, single-user tool: a plain lock + dict is enough to prevent
# two overlapping runs (a normal sync and a full audit share this same lock,
# so they can never run concurrently either) and to let /status report the
# outcome of the last one. No queue/worker infra needed for "click a button,
# run once."
_lock = threading.Lock()
_state: dict = {"in_progress": False, "last_error": None, "current_run_type": None, "stopping": False}


class SyncStatusResponse(BaseModel):
    in_progress: bool
    last_error: str | None
    current_run_type: str | None
    stopping: bool
    latest_run: PipelineRun | None
    history: list[PipelineRun]


class SyncStartResponse(BaseModel):
    status: str


def get_run_sync():
    """Named (not an inline lambda) so tests can import it and override it
    via app.dependency_overrides, same as get_gmail_client/get_llm_model."""
    return _default_run_sync


def get_full_audit():
    """Same dependency-injection pattern as get_run_sync, for the full-audit
    trigger below."""
    return _default_full_audit


def _run_in_background(fn, settings: Settings) -> None:
    try:
        fn(settings)
    except Exception as exc:  # noqa: BLE001 - must not crash the background thread silently
        # logger.exception (not .warning/.error) captures the full traceback -
        # this was previously missing entirely, so a background sync/full-audit
        # failure left no trace anywhere except the generic message in
        # _state["last_error"], which the dashboard only ever shows as a
        # plain-language toast. The real cause needs to be visible in the
        # server terminal for anyone actually debugging a failed run.
        logger.exception("Background %s failed", fn.__name__)
        with _lock:
            _state["last_error"] = str(exc)
    finally:
        with _lock:
            _state["in_progress"] = False
            _state["current_run_type"] = None
            _state["stopping"] = False
        clear_cancel()


def _start(fn, settings: Settings, run_type: str) -> None:
    with _lock:
        if _state["in_progress"]:
            raise HTTPException(status_code=409, detail="A sync is already in progress")
        _state["in_progress"] = True
        _state["last_error"] = None
        _state["current_run_type"] = run_type
        _state["stopping"] = False
    clear_cancel()  # defensive: a fresh run must never start already-cancelled
    threading.Thread(target=_run_in_background, args=(fn, settings), daemon=True).start()


def register_sync_routes(
    app, *, get_session, get_settings, get_run_sync=get_run_sync, get_full_audit=get_full_audit
) -> None:
    """get_run_sync/get_full_audit default to the real pipeline entrypoints
    but are dependency-injected (same pattern as get_gmail_client/
    get_llm_model) so tests can swap in a fake that never touches real
    Gmail/LLM calls.
    """

    @router.post(
        "",
        response_model=SyncStartResponse,
        status_code=202,
        summary="Trigger a manual pipeline sync in the background",
        responses={409: {"description": "A sync is already in progress"}},
    )
    def start_sync(settings: Settings = Depends(get_settings), run_sync_fn=Depends(get_run_sync)):
        _start(run_sync_fn, settings, "incremental")
        return {"status": "started"}

    @router.post(
        "/full-audit",
        response_model=SyncStartResponse,
        status_code=202,
        summary="Trigger a full-audit revalidation of every email ever seen, in the background",
        responses={409: {"description": "A sync is already in progress"}},
    )
    def start_full_audit(settings: Settings = Depends(get_settings), full_audit_fn=Depends(get_full_audit)):
        _start(full_audit_fn, settings, "full_audit")
        return {"status": "started"}

    @router.post(
        "/stop",
        response_model=SyncStartResponse,
        summary="Request the in-progress sync/full-audit to stop after the email it's currently on",
        responses={409: {"description": "No sync is currently in progress"}},
    )
    def stop_sync():
        with _lock:
            if not _state["in_progress"]:
                raise HTTPException(status_code=409, detail="No sync is currently in progress")
            _state["stopping"] = True
        # Cooperative only: the background thread checks this between emails
        # (see process_emails/process_full_audit), not instantly - a stop
        # finishes whatever email is already in flight first. See
        # run_control.py for why an instant abort isn't attempted.
        request_cancel()
        return {"status": "stopping"}

    @router.get(
        "/stream",
        summary="Server-Sent Events stream of which pipeline node just ran, for the /sync page's live graph",
    )
    async def sync_stream(request: Request):
        """One SSE event per node execution (see observability.publish_node_event,
        called unconditionally from process_emails/process_full_audit's own
        node loop - this route is the only consumer, everything upstream of
        it is a no-op when nobody's subscribed). Diagnostic/UI-only, same as
        Langfuse tracing: nothing about the pipeline's behavior depends on
        whether this stream has a listener.
        """
        subscriber = subscribe_to_node_events()

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.to_thread(subscriber.get, True, _STREAM_KEEPALIVE_SECONDS)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                unsubscribe_from_node_events(subscriber)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get(
        "/status",
        response_model=SyncStatusResponse,
        summary="Poll the currently running (or most recently finished) sync",
    )
    def sync_status(session: Session = Depends(get_session)):
        with _lock:
            in_progress = _state["in_progress"]
            last_error = _state["last_error"]
            current_run_type = _state["current_run_type"] if in_progress else None
            stopping = _state["stopping"] if in_progress else False
        return {
            "in_progress": in_progress,
            "last_error": last_error,
            "current_run_type": current_run_type,
            "stopping": stopping,
            "latest_run": repo.get_latest_pipeline_run(session),
            "history": repo.list_recent_pipeline_runs(session, limit=10),
        }

    app.include_router(router)
