import threading
import time

from applysync.web.sync import _state, get_run_sync


def _reset_state():
    _state["in_progress"] = False
    _state["last_error"] = None


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_sync_status_when_never_run(client):
    _reset_state()
    response = client.get("/api/sync/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {"in_progress": False, "last_error": None, "latest_run": None}


def test_start_sync_runs_in_background_and_reports_completion(client):
    _reset_state()
    hold = threading.Event()
    calls = []

    def fake_run_sync(settings):
        calls.append(settings)
        hold.wait(timeout=5)
        return {"run_id": "fake", "emails_fetched": 0, "emails_relevant": 0, "applications_created": 0, "events_created": 0}

    client.app.dependency_overrides[get_run_sync] = lambda: fake_run_sync

    response = client.post("/api/sync")
    assert response.status_code == 202
    assert response.json() == {"status": "started"}

    assert _wait_until(lambda: len(calls) == 1)

    # A second sync while the first is still running (blocked on `hold`) must
    # be rejected, not queued or run concurrently.
    conflict = client.post("/api/sync")
    assert conflict.status_code == 409

    status = client.get("/api/sync/status").json()
    assert status["in_progress"] is True

    hold.set()
    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])

    final_status = client.get("/api/sync/status").json()
    assert final_status["in_progress"] is False
    assert final_status["last_error"] is None


def test_start_sync_records_error_on_failure(client):
    _reset_state()

    def failing_run_sync(settings):
        raise RuntimeError("Gmail API unreachable")

    client.app.dependency_overrides[get_run_sync] = lambda: failing_run_sync

    response = client.post("/api/sync")
    assert response.status_code == 202

    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])

    status = client.get("/api/sync/status").json()
    assert status["in_progress"] is False
    assert status["last_error"] == "Gmail API unreachable"


def test_latest_pipeline_run_reflected_in_status(client):
    _reset_state()
    from applysync.db import repository as repo

    run = repo.create_pipeline_run(client.db_session, "run-xyz")
    repo.update_pipeline_run_progress(
        client.db_session, run.id, emails_total=3, emails_scrutinized=3, emails_extracted=2, emails_written=2
    )
    repo.finish_pipeline_run(
        client.db_session,
        run.id,
        emails_fetched=3,
        emails_relevant=2,
        applications_created=1,
        events_created=1,
    )

    status = client.get("/api/sync/status").json()
    assert status["latest_run"]["id"] == "run-xyz"
    assert status["latest_run"]["emails_fetched"] == 3
    assert status["latest_run"]["finished_at"] is not None
    assert status["latest_run"]["emails_total"] == 3
    assert status["latest_run"]["emails_scrutinized"] == 3
    assert status["latest_run"]["emails_extracted"] == 2
    assert status["latest_run"]["emails_written"] == 2
