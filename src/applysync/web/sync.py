from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from applysync.config import Settings
from applysync.db import repository as repo
from applysync.db.models import PipelineRun
from applysync.pipeline.graph import run_sync as _default_run_sync

router = APIRouter(prefix="/api/sync", tags=["sync"])

# Single-process, single-user tool: a plain lock + dict is enough to prevent
# two overlapping runs and to let /status report the outcome of the last one.
# No queue/worker infra needed for "click a button, run once."
_lock = threading.Lock()
_state: dict = {"in_progress": False, "last_error": None}


class SyncStatusResponse(BaseModel):
    in_progress: bool
    last_error: str | None
    latest_run: PipelineRun | None
    history: list[PipelineRun]


class SyncStartResponse(BaseModel):
    status: str


def get_run_sync():
    """Named (not an inline lambda) so tests can import it and override it
    via app.dependency_overrides, same as get_gmail_client/get_llm_model."""
    return _default_run_sync


def _run_in_background(run_sync_fn, settings: Settings) -> None:
    try:
        run_sync_fn(settings)
    except Exception as exc:  # noqa: BLE001 - surfaced via /status, must not crash the background thread silently
        _state["last_error"] = str(exc)
    finally:
        _state["in_progress"] = False


def register_sync_routes(app, *, get_session, get_settings, get_run_sync=get_run_sync) -> None:
    """get_run_sync defaults to the real pipeline entrypoint but is
    dependency-injected (same pattern as get_gmail_client/get_llm_model) so
    tests can swap in a fake that never touches real Gmail/LLM calls.
    """

    @router.post(
        "",
        response_model=SyncStartResponse,
        status_code=202,
        summary="Trigger a manual pipeline sync in the background",
        responses={409: {"description": "A sync is already in progress"}},
    )
    def start_sync(settings: Settings = Depends(get_settings), run_sync_fn=Depends(get_run_sync)):
        with _lock:
            if _state["in_progress"]:
                raise HTTPException(status_code=409, detail="A sync is already in progress")
            _state["in_progress"] = True
            _state["last_error"] = None
        threading.Thread(target=_run_in_background, args=(run_sync_fn, settings), daemon=True).start()
        return {"status": "started"}

    @router.get(
        "/status",
        response_model=SyncStatusResponse,
        summary="Poll the currently running (or most recently finished) sync",
    )
    def sync_status(session: Session = Depends(get_session)):
        return {
            "in_progress": _state["in_progress"],
            "last_error": _state["last_error"],
            "latest_run": repo.get_latest_pipeline_run(session),
            "history": repo.list_recent_pipeline_runs(session, limit=10),
        }

    app.include_router(router)
