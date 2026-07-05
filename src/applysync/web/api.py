from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from applysync.db import repository as repo
from applysync.gmail.client import GmailClient
from applysync.pipeline.graph import reprocess_application

router = APIRouter(prefix="/api")


class StatusUpdate(BaseModel):
    status: str


class FieldsUpdate(BaseModel):
    company_name: str
    job_title: str
    platform: str


def register_api_routes(app, *, get_session, get_gmail_client, get_llm_model) -> None:
    """Registers the JSON API on `app`. Takes the dependency callables as
    params rather than importing them, so this module doesn't need to import
    back from web.app (which imports this module) and create a cycle.
    """

    @router.get("/dashboard")
    def get_dashboard(
        session: Session = Depends(get_session),
        year: int | None = None,
        platform: str | None = None,
        company: str | None = None,
        status: str | None = None,
    ):
        applications = repo.filtered_applications(
            session, year=year, platform=platform, company=company, status=status
        )
        filtered_ids = {a.id for a in applications}
        reminders = [a for a in repo.stale_applications(session) if a.id in filtered_ids]
        return {
            "board": repo.applications_by_status(applications),
            "status_order": repo.STATUS_ORDER,
            "breakdown": repo.platform_breakdown(applications),
            "reminders": reminders,
            "filter_options": repo.filter_options(session),
        }

    @router.get("/applications/{application_id}")
    def get_application_detail(application_id: int, session: Session = Depends(get_session)):
        application = repo.get_application(session, application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return {
            "application": application,
            "timeline": repo.application_timeline(session, application_id),
        }

    @router.patch("/applications/{application_id}/status")
    def patch_status(application_id: int, body: StatusUpdate, session: Session = Depends(get_session)):
        application = repo.set_manual_status(session, application_id, body.status)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return application

    @router.patch("/applications/{application_id}")
    def patch_fields(application_id: int, body: FieldsUpdate, session: Session = Depends(get_session)):
        application = repo.update_application_fields(
            session,
            application_id,
            company_name=body.company_name,
            job_title=body.job_title,
            platform=body.platform,
        )
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return application

    @router.post("/applications/{application_id}/reprocess")
    def post_reprocess(
        application_id: int,
        session: Session = Depends(get_session),
        gmail_client: GmailClient = Depends(get_gmail_client),
        model=Depends(get_llm_model),
    ):
        application = reprocess_application(session, application_id, gmail_client=gmail_client, model=model)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return application

    app.include_router(router)
