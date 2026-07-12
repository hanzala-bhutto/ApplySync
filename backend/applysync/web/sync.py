from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from applysync.config import Settings
from applysync.db import repository as repo
from applysync.db.models import PipelineRun
from applysync.pipeline.full_scan import full_scan as _default_full_scan
from applysync.pipeline.graph import run_sync as _default_run_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])

# Single-process, single-user tool: a plain lock + dict is enough to prevent
# two overlapping runs (a normal sync and a full scan share this same lock,
# so they can never run concurrently either) and to let /status report the
# outcome of the last one. No queue/worker infra needed for "click a button,
# run once."
_lock = threading.Lock()
_state: dict = {"in_progress": False, "last_error": None, "current_run_type": None}


class SyncStatusResponse(BaseModel):
    in_progress: bool
    last_error: str | None
    current_run_type: str | None
    latest_run: PipelineRun | None
    history: list[PipelineRun]


class SyncStartResponse(BaseModel):
    status: str


def get_run_sync():
    """Named (not an inline lambda) so tests can import it and override it
    via app.dependency_overrides, same as get_gmail_client/get_llm_model."""
    return _default_run_sync


def get_full_scan():
    """Same dependency-injection pattern as get_run_sync, for the full-scan
    trigger below."""
    return _default_full_scan


def _run_in_background(fn, settings: Settings) -> None:
    try:
        fn(settings)
    except Exception as exc:  # noqa: BLE001 - must not crash the background thread silently
        # logger.exception (not .warning/.error) captures the full traceback -
        # this was previously missing entirely, so a background sync/full-scan
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


def _start(fn, settings: Settings, run_type: str) -> None:
    with _lock:
        if _state["in_progress"]:
            raise HTTPException(status_code=409, detail="A sync is already in progress")
        _state["in_progress"] = True
        _state["last_error"] = None
        _state["current_run_type"] = run_type
    threading.Thread(target=_run_in_background, args=(fn, settings), daemon=True).start()


def register_sync_routes(
    app, *, get_session, get_settings, get_run_sync=get_run_sync, get_full_scan=get_full_scan
) -> None:
    """get_run_sync/get_full_scan default to the real pipeline entrypoints
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
        "/full-scan",
        response_model=SyncStartResponse,
        status_code=202,
        summary="Trigger a full-scan revalidation of every email ever seen, in the background",
        responses={409: {"description": "A sync is already in progress"}},
    )
    def start_full_scan(settings: Settings = Depends(get_settings), full_scan_fn=Depends(get_full_scan)):
        _start(full_scan_fn, settings, "full_scan")
        return {"status": "started"}

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
        return {
            "in_progress": in_progress,
            "last_error": last_error,
            "current_run_type": current_run_type,
            "latest_run": repo.get_latest_pipeline_run(session),
            "history": repo.list_recent_pipeline_runs(session, limit=10),
        }

    app.include_router(router)
