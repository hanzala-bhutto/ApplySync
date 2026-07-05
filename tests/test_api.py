import base64
from datetime import date, datetime

from applysync.db import repository as repo
from applysync.pipeline.state import ClassifyAndExtractResult
from applysync.web.app import get_gmail_client, get_llm_model
from tests.fakes import FakeExtractModel, FakeGmailClient, FakeStructuredModel


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def test_get_dashboard_empty(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert set(body["board"].keys()) == set(repo.STATUS_ORDER)
    assert body["reminders"] == []
    assert body["breakdown"] == []


def test_get_dashboard_returns_application_in_its_status_column(client):
    repo.create_application(
        client.db_session, company_name="Acme", job_title="Engineer", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="interview",
    )

    body = client.get("/api/dashboard").json()

    assert [a["company_name"] for a in body["board"]["interview"]] == ["Acme"]


def test_get_dashboard_filters_by_platform(client):
    repo.create_application(
        client.db_session, company_name="Acme", job_title="A", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )
    repo.create_application(
        client.db_session, company_name="Beta", job_title="B", platform="indeed",
        applied_date=date(2026, 1, 1), current_status="applied",
    )

    body = client.get("/api/dashboard?platform=linkedin").json()

    names = [a["company_name"] for col in body["board"].values() for a in col]
    assert names == ["Acme"]


def test_get_application_detail(client):
    application = repo.create_application(
        client.db_session, company_name="Acme", job_title="Engineer", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )
    repo.add_status_event(
        client.db_session, application_id=application.id, status="applied",
        event_date=datetime(2026, 1, 1), source_email_id="msg-1",
    )

    response = client.get(f"/api/applications/{application.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["application"]["company_name"] == "Acme"
    assert len(body["timeline"]) == 1


def test_get_application_detail_404(client):
    response = client.get("/api/applications/999")
    assert response.status_code == 404


def test_patch_status(client):
    application = repo.create_application(
        client.db_session, company_name="Acme", job_title="Engineer", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )

    response = client.patch(f"/api/applications/{application.id}/status", json={"status": "interview"})

    assert response.status_code == 200
    assert response.json()["current_status"] == "interview"


def test_patch_status_404(client):
    response = client.patch("/api/applications/999/status", json={"status": "interview"})
    assert response.status_code == 404


def test_patch_fields(client):
    application = repo.create_application(
        client.db_session, company_name="EGYM", job_title="(unspecified role)", platform="other",
        applied_date=date(2026, 1, 1), current_status="applied",
    )

    response = client.patch(
        f"/api/applications/{application.id}",
        json={"company_name": "EGYM SE", "job_title": "Backend Engineer", "platform": "other"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["company_name"] == "EGYM SE"
    assert body["job_title"] == "Backend Engineer"


def test_post_reprocess(client):
    application = repo.create_application(
        client.db_session, company_name="Acme", job_title="(unspecified role)", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )
    repo.add_status_event(
        client.db_session, application_id=application.id, status="applied",
        event_date=datetime(2026, 1, 1), source_email_id="msg-1",
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
    extracted = ClassifyAndExtractResult(
        is_relevant=True, company_name="Acme", job_title="Backend Engineer", status="interview"
    )
    client.app.dependency_overrides[get_gmail_client] = lambda: FakeGmailClient(raw_message)
    client.app.dependency_overrides[get_llm_model] = lambda: FakeExtractModel(FakeStructuredModel(result=extracted))

    response = client.post(f"/api/applications/{application.id}/reprocess")

    assert response.status_code == 200
    body = response.json()
    assert body["job_title"] == "Backend Engineer"
    assert body["current_status"] == "interview"
