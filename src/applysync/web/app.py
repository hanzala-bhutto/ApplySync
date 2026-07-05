from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from applysync.config import get_settings
from applysync.db import repository as repo
from applysync.db.init_db import get_engine, init_db

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_session():
    settings = get_settings()
    init_db(settings.db_path)
    with Session(get_engine(settings.db_path)) as session:
        yield session


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
                "breakdown": repo.platform_breakdown(session),
                "reminders": repo.stale_applications(session),
            },
        )

    @app.get("/applications/{application_id}", response_class=HTMLResponse)
    def application_detail(request: Request, application_id: int, session: Session = Depends(get_session)):
        application = session.get(repo.Application, application_id)
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
            },
        )

    return app


app = create_app()
