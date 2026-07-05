import base64
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from applysync.db import repository as repo
from applysync.pipeline.state import JobApplicationEvent
from applysync.web.app import create_app, get_gmail_client, get_llm_model, get_session
from tests.fakes import FakeExtractModel, FakeStructuredModel


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class FakeGmailService:
    def __init__(self, raw_message: dict):
        self._raw_message = raw_message

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format):
        return self

    def execute(self):
        return self._raw_message


class FakeGmailClient:
    def __init__(self, raw_message: dict):
        self.service = FakeGmailService(raw_message)


@pytest.fixture
def client():
    # StaticPool: FastAPI's TestClient runs sync routes on a worker thread,
    # and without a shared single connection, a fresh (tableless) in-memory
    # SQLite DB gets created for that thread instead of reusing this one.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    test_session = Session(engine)

    app = create_app()

    def override_get_session():
        yield test_session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as c:
        c.db_session = test_session
        yield c

    test_session.close()


def test_dashboard_loads_with_no_applications(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ApplySync" in response.text
    assert "Nothing stale right now" in response.text


def test_dashboard_shows_application_in_its_status_column(client):
    repo.create_application(
        client.db_session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="interview",
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "Acme Corp" in response.text
    assert "Backend Engineer" in response.text


def test_dashboard_shows_stale_application_as_reminder(client):
    repo.create_application(
        client.db_session,
        company_name="OldCo",
        job_title="Role",
        platform="indeed",
        applied_date=date(2020, 1, 1),
        current_status="applied",
    )

    response = client.get("/")

    assert "OldCo" in response.text
    assert "Nothing stale right now" not in response.text


def test_application_detail_shows_timeline(client):
    application = repo.create_application(
        client.db_session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="interview",
    )
    repo.add_status_event(
        client.db_session,
        application_id=application.id,
        status="interview",
        event_date=datetime(2026, 1, 10),
        source_email_id="msg-1",
    )

    response = client.get(f"/applications/{application.id}")

    assert response.status_code == 200
    assert "Acme Corp" in response.text
    assert "interview" in response.text


def test_application_detail_404_for_unknown_id(client):
    response = client.get("/applications/999")
    assert response.status_code == 404


def test_patch_status_updates_status_from_drag_and_drop(client):
    application = repo.create_application(
        client.db_session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    response = client.patch(f"/applications/{application.id}/status", data={"status": "interview"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "interview"}
    client.db_session.refresh(application)
    assert application.current_status == "interview"


def test_patch_status_404_for_unknown_id(client):
    response = client.patch("/applications/999/status", data={"status": "interview"})
    assert response.status_code == 404


def test_patch_fields_updates_and_returns_fragment(client):
    application = repo.create_application(
        client.db_session,
        company_name="EGYM",
        job_title="(unspecified role)",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    response = client.patch(
        f"/applications/{application.id}",
        data={"company_name": "EGYM SE", "job_title": "Backend Engineer", "platform": "other"},
    )

    assert response.status_code == 200
    assert "EGYM SE" in response.text
    assert "Backend Engineer" in response.text
    client.db_session.refresh(application)
    assert application.company_name == "EGYM SE"
    assert application.job_title == "Backend Engineer"


def test_reprocess_refetches_email_and_updates_fields(client):
    application = repo.create_application(
        client.db_session,
        company_name="Acme",
        job_title="(unspecified role)",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.add_status_event(
        client.db_session,
        application_id=application.id,
        status="applied",
        event_date=datetime(2026, 1, 1),
        source_email_id="msg-1",
    )

    raw_message = {
        "id": "msg-1",
        "threadId": "thread-1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "jobs@acme.example"},
                {"name": "Subject", "value": "Your application"},
                {"name": "Date", "value": "Wed, 1 Jan 2026 09:00:00 +0000"},
            ],
            "body": {"data": _b64("Thanks for applying to Backend Engineer at Acme.")},
        },
    }
    extracted = JobApplicationEvent(company_name="Acme", job_title="Backend Engineer", status="interview")

    client.app.dependency_overrides[get_gmail_client] = lambda: FakeGmailClient(raw_message)
    client.app.dependency_overrides[get_llm_model] = lambda: FakeExtractModel(
        FakeStructuredModel(result=extracted)
    )

    response = client.post(f"/applications/{application.id}/reprocess")

    assert response.status_code == 200
    assert "Backend Engineer" in response.text
    client.db_session.refresh(application)
    assert application.job_title == "Backend Engineer"
    assert application.current_status == "interview"


def test_reprocess_404_for_unknown_id(client):
    response = client.post("/applications/999/reprocess")
    assert response.status_code == 404
