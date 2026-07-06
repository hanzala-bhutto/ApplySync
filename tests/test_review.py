from datetime import date

from applysync.db import repository as repo


def test_list_review_suggestions_empty_when_none_pending(client):
    response = client.get("/api/review-suggestions")
    assert response.status_code == 200
    assert response.json() == []


def test_list_review_suggestions_returns_pending_only(client):
    repo.create_pipeline_run(client.db_session, "run-1", run_type="full_scan")
    repo.create_review_suggestion(
        client.db_session,
        message_id="msg-1",
        action="new_application",
        previous_classification="irrelevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "applied", "platform": "linkedin"}',
        pipeline_run_id="run-1",
    )
    resolved = repo.create_review_suggestion(
        client.db_session,
        message_id="msg-2",
        action="new_application",
        previous_classification="irrelevant",
        suggested_classification="relevant",
        suggested_extract_json="{}",
        pipeline_run_id="run-1",
    )
    repo.reject_review_suggestion(client.db_session, resolved.id)

    response = client.get("/api/review-suggestions")

    body = response.json()
    assert len(body) == 1
    assert body[0]["message_id"] == "msg-1"


def test_approve_review_suggestion_applies_change(client):
    repo.create_pipeline_run(client.db_session, "run-1", run_type="full_scan")
    suggestion = repo.create_review_suggestion(
        client.db_session,
        message_id="msg-1",
        action="new_application",
        previous_classification="irrelevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "applied", "platform": "linkedin"}',
        pipeline_run_id="run-1",
    )

    response = client.post(f"/api/review-suggestions/{suggestion.id}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    application = repo.find_matching_application(client.db_session, "Acme", "Engineer", "linkedin")
    assert application is not None


def test_reject_review_suggestion_leaves_data_untouched(client):
    application = repo.create_application(
        client.db_session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.create_pipeline_run(client.db_session, "run-1", run_type="full_scan")
    suggestion = repo.create_review_suggestion(
        client.db_session,
        message_id="msg-1",
        application_id=application.id,
        action="update_existing",
        previous_classification="relevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "rejected", "platform": "linkedin"}',
        pipeline_run_id="run-1",
    )

    response = client.post(f"/api/review-suggestions/{suggestion.id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    client.db_session.refresh(application)
    assert application.current_status == "applied"


def test_approve_unknown_suggestion_returns_404(client):
    response = client.post("/api/review-suggestions/999/approve")
    assert response.status_code == 404


def test_reject_unknown_suggestion_returns_404(client):
    response = client.post("/api/review-suggestions/999/reject")
    assert response.status_code == 404
