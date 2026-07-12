from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from applysync.db import repository as repo
from applysync.db.models import Application, StatusEvent
from applysync.gmail.client import GmailClient
from applysync.pipeline.graph import reprocess_application
from applysync.research import ResearchError, research_company
from applysync.search import SearxngClient

router = APIRouter(prefix="/api", tags=["applications"])


class StatusUpdate(BaseModel):
    status: str


class FieldsUpdate(BaseModel):
    company_name: str
    job_title: str
    platform: str


class FilterOptionsResponse(BaseModel):
    years: list[int]
    platforms: list[str]
    statuses: list[str]


class PlatformBreakdownRow(BaseModel):
    platform: str
    total: int
    responded: int


class DashboardResponse(BaseModel):
    board: dict[str, list[Application]]
    status_order: list[str]
    breakdown: list[PlatformBreakdownRow]
    reminders: list[Application]
    reminders_total: int
    filter_options: FilterOptionsResponse


class ReminderPageResponse(BaseModel):
    items: list[Application]
    total: int
    page: int
    page_size: int


class SourceEmailResponse(BaseModel):
    subject: str
    sender: str
    date: str
    body: str


REMINDERS_PREVIEW_SIZE = 6


class ApplicationDetailResponse(BaseModel):
    application: Application
    timeline: list[StatusEvent]


class CompanyResearchResponse(BaseModel):
    """A web-researched company profile. Kept explicitly separate from
    ApplicationDetailResponse so the frontend never renders these fields as if
    they were the application's own extracted data: everything here is
    web-sourced, verifiable via source_urls, and clearly labeled as such."""

    company_name: str
    summary: str | None
    industry: str | None
    company_size: str | None
    headquarters: str | None
    website: str | None
    recent_news: str | None
    source_urls: list[str]
    researched_at: str


def _profile_to_response(profile) -> dict:
    return {
        "company_name": profile.display_name,
        "summary": profile.summary,
        "industry": profile.industry,
        "company_size": profile.company_size,
        "headquarters": profile.headquarters,
        "website": profile.website,
        "recent_news": profile.recent_news,
        "source_urls": json.loads(profile.source_urls_json) if profile.source_urls_json else [],
        "researched_at": profile.researched_at.isoformat(),
    }


def register_api_routes(
    app, *, get_session, get_gmail_client, get_llm_model, get_search_client
) -> None:
    """Registers the JSON API on `app`. Takes the dependency callables as
    params rather than importing them, so this module doesn't need to import
    back from web.app (which imports this module) and create a cycle.
    """

    @router.get(
        "/dashboard",
        response_model=DashboardResponse,
        summary="Get the full dashboard: pipeline board, reminders, and platform breakdown",
    )
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
            "reminders": reminders[:REMINDERS_PREVIEW_SIZE],
            "reminders_total": len(reminders),
            "filter_options": repo.filter_options(session),
        }

    @router.get(
        "/reminders",
        response_model=ReminderPageResponse,
        summary="Paginated list of applications needing follow-up (no filters, DB-paginated for scale)",
    )
    def get_reminders(
        session: Session = Depends(get_session),
        page: int = 1,
        page_size: int = 20,
    ):
        offset = (page - 1) * page_size
        items = repo.stale_applications_page(session, offset=offset, limit=page_size)
        total = repo.stale_applications_count(session)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @router.get(
        "/applications/{application_id}",
        response_model=ApplicationDetailResponse,
        summary="Get a single application and its status timeline",
        responses={404: {"description": "Application not found"}},
    )
    def get_application_detail(application_id: int, session: Session = Depends(get_session)):
        application = repo.get_application(session, application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return {
            "application": application,
            "timeline": repo.application_timeline(session, application_id),
        }

    @router.get(
        "/status-events/{event_id}/email",
        response_model=SourceEmailResponse,
        summary="Fetch the original Gmail message behind a status event, for human verification against the extracted fields",
        responses={404: {"description": "Status event not found, or it has no source email (a manual correction)"}},
    )
    def get_status_event_email(
        event_id: int,
        session: Session = Depends(get_session),
        gmail_client: GmailClient = Depends(get_gmail_client),
    ):
        event = repo.get_status_event(session, event_id)
        if event is None or event.source_email_id is None:
            raise HTTPException(status_code=404, detail="No source email for this event")
        email = gmail_client.get_message(event.source_email_id)
        return {"subject": email.subject, "sender": email.sender, "date": email.date, "body": email.body}

    @router.patch(
        "/applications/{application_id}/status",
        response_model=Application,
        summary="Correct an application's status (e.g. drag-and-drop between Kanban columns)",
        responses={404: {"description": "Application not found"}},
    )
    def patch_status(application_id: int, body: StatusUpdate, session: Session = Depends(get_session)):
        application = repo.set_manual_status(session, application_id, body.status)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        return application

    @router.patch(
        "/applications/{application_id}",
        response_model=Application,
        summary="Correct an application's extracted fields (company, title, platform)",
        responses={404: {"description": "Application not found"}},
    )
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

    @router.post(
        "/applications/{application_id}/reprocess",
        response_model=Application,
        summary="Re-run extraction on the original email behind this application",
        responses={404: {"description": "Application not found"}},
    )
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

    @router.post(
        "/applications/{application_id}/research",
        response_model=CompanyResearchResponse,
        summary="Web-research the application's company (cached; pass refresh=true to re-fetch)",
        responses={
            404: {"description": "Application not found"},
            502: {"description": "Web research failed (search or synthesis error)"},
        },
    )
    def post_research_company(
        application_id: int,
        refresh: bool = False,
        session: Session = Depends(get_session),
        search_client: SearxngClient = Depends(get_search_client),
        model=Depends(get_llm_model),
    ):
        application = repo.get_application(session, application_id)
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")

        cached = repo.get_company_profile(session, application.company_name)
        if cached is not None and not refresh:
            return _profile_to_response(cached)

        try:
            profile, source_urls = research_company(
                application.company_name, search_client=search_client, model=model
            )
        except ResearchError as exc:
            # Plain-language message, not the raw exception - matches the
            # project's mutation-feedback rule. The specifics are logged
            # server-side by research_company itself.
            raise HTTPException(
                status_code=502, detail="Could not research this company right now."
            ) from exc

        stored = repo.upsert_company_profile(
            session,
            display_name=application.company_name,
            summary=profile.summary,
            industry=profile.industry,
            company_size=profile.company_size,
            headquarters=profile.headquarters,
            website=profile.website,
            recent_news=profile.recent_news,
            source_urls=source_urls,
        )
        return _profile_to_response(stored)

    app.include_router(router)
