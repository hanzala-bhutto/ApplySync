from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from applysync.db import repository as repo
from applysync.web.app import create_app, get_session


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
