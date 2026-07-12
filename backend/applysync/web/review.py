from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from applysync.db import repository as repo
from applysync.db.models import ReviewSuggestion

router = APIRouter(prefix="/api/review-suggestions", tags=["review"])


def register_review_routes(app, *, get_session) -> None:
    @router.get(
        "",
        response_model=list[ReviewSuggestion],
        summary="List pending full-scan review suggestions",
    )
    def list_review_suggestions(session: Session = Depends(get_session)):
        return repo.list_pending_review_suggestions(session)

    @router.post(
        "/{suggestion_id}/approve",
        response_model=ReviewSuggestion,
        summary="Apply a review suggestion's proposed change",
        responses={404: {"description": "No such review suggestion"}},
    )
    def approve(suggestion_id: int, session: Session = Depends(get_session)):
        try:
            return repo.approve_review_suggestion(session, suggestion_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/{suggestion_id}/reject",
        response_model=ReviewSuggestion,
        summary="Dismiss a review suggestion without changing any data",
        responses={404: {"description": "No such review suggestion"}},
    )
    def reject(suggestion_id: int, session: Session = Depends(get_session)):
        try:
            return repo.reject_review_suggestion(session, suggestion_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.include_router(router)
