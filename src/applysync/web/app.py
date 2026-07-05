from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from applysync.config import get_settings
from applysync.db import repository as repo
from applysync.db.init_db import get_engine, init_db
from applysync.gmail.client import GmailClient
from applysync.llm import get_chat_model
from applysync.pipeline.graph import reprocess_application

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Tailwind utility classes per status, used for column headers and card
# badges. "other" also serves as the fallback for any status not in
# STATUS_ORDER (there shouldn't be one, but nothing enforces that at the DB
# layer).
STATUS_STYLES = {
    "applied": {"bg": "bg-slate-100 dark:bg-slate-800", "text": "text-slate-700 dark:text-slate-300", "dot": "bg-slate-400"},
    "viewed": {"bg": "bg-sky-50 dark:bg-sky-950", "text": "text-sky-700 dark:text-sky-300", "dot": "bg-sky-500"},
    "assessment": {"bg": "bg-cyan-50 dark:bg-cyan-950", "text": "text-cyan-700 dark:text-cyan-300", "dot": "bg-cyan-500"},
    "interview": {"bg": "bg-violet-50 dark:bg-violet-950", "text": "text-violet-700 dark:text-violet-300", "dot": "bg-violet-500"},
    "offer": {"bg": "bg-emerald-50 dark:bg-emerald-950", "text": "text-emerald-700 dark:text-emerald-300", "dot": "bg-emerald-500"},
    "rejected": {"bg": "bg-rose-50 dark:bg-rose-950", "text": "text-rose-700 dark:text-rose-300", "dot": "bg-rose-500"},
    "other": {"bg": "bg-amber-50 dark:bg-amber-950", "text": "text-amber-700 dark:text-amber-300", "dot": "bg-amber-500"},
}


def get_session():
    settings = get_settings()
    init_db(settings.db_path)
    with Session(get_engine(settings.db_path)) as session:
        yield session


def get_gmail_client() -> GmailClient:
    return GmailClient(get_settings())


def get_llm_model():
    return get_chat_model(get_settings())


def create_app() -> FastAPI:
    app = FastAPI(title="ApplySync")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, session: Session = Depends(get_session)):
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "board": repo.applications_by_status(session),
                "status_order": repo.STATUS_ORDER,
                "status_styles": STATUS_STYLES,
                "breakdown": repo.platform_breakdown(session),
                "reminders": repo.stale_applications(session),
            },
        )

    @app.get("/applications/{application_id}", response_class=HTMLResponse)
    def application_detail(request: Request, application_id: int, session: Session = Depends(get_session)):
        application = repo.get_application(session, application_id)
        if application is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"application_id": application_id}, status_code=404
            )
        return templates.TemplateResponse(
            request,
            "application_detail.html",
            {
                "application": application,
                "timeline": repo.application_timeline(session, application_id),
                "status_order": repo.STATUS_ORDER,
                "status_styles": STATUS_STYLES,
            },
        )

    def _detail_fragment(request: Request, application, session: Session):
        return templates.TemplateResponse(
            request,
            "partials/application_detail_content.html",
            {
                "application": application,
                "timeline": repo.application_timeline(session, application.id),
                "status_order": repo.STATUS_ORDER,
                "status_styles": STATUS_STYLES,
            },
        )

    @app.patch("/applications/{application_id}/status")
    def update_status(application_id: int, status: str = Form(...), session: Session = Depends(get_session)):
        application = repo.set_manual_status(session, application_id, status)
        if application is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "status": application.current_status})

    @app.patch("/applications/{application_id}", response_class=HTMLResponse)
    def update_fields(
        request: Request,
        application_id: int,
        company_name: str = Form(...),
        job_title: str = Form(...),
        platform: str = Form(...),
        session: Session = Depends(get_session),
    ):
        application = repo.update_application_fields(
            session, application_id, company_name=company_name, job_title=job_title, platform=platform
        )
        if application is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"application_id": application_id}, status_code=404
            )
        return _detail_fragment(request, application, session)

    @app.post("/applications/{application_id}/reprocess", response_class=HTMLResponse)
    def reprocess(
        request: Request,
        application_id: int,
        session: Session = Depends(get_session),
        gmail_client: GmailClient = Depends(get_gmail_client),
        model=Depends(get_llm_model),
    ):
        application = reprocess_application(
            session, application_id, gmail_client=gmail_client, model=model
        )
        if application is None:
            return templates.TemplateResponse(
                request, "not_found.html", {"application_id": application_id}, status_code=404
            )
        return _detail_fragment(request, application, session)

    return app


app = create_app()
