import threading
import time

from applysync.web.sync import _state, get_full_audit, get_run_sync


def _reset_state():
    _state["in_progress"] = False
    _state["last_error"] = None
    _state["current_run_type"] = None
    _state["stopping"] = False


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
    assert body == {
        "in_progress": False,
        "last_error": None,
        "current_run_type": None,
        "stopping": False,
        "latest_run": None,
        "history": [],
    }


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


def test_sync_status_includes_recent_run_history(client):
    _reset_state()
    from applysync.db import repository as repo

    for i in range(3):
        run = repo.create_pipeline_run(client.db_session, f"run-{i}")
        repo.finish_pipeline_run(
            client.db_session,
            run.id,
            emails_fetched=1,
            emails_relevant=1,
            applications_created=1,
            events_created=1,
        )

    status = client.get("/api/sync/status").json()
    assert len(status["history"]) == 3
    assert {run["id"] for run in status["history"]} == {"run-0", "run-1", "run-2"}


def test_start_full_audit_runs_in_background_and_reports_run_type(client):
    _reset_state()
    hold = threading.Event()
    calls = []

    def fake_full_audit(settings):
        calls.append(settings)
        hold.wait(timeout=5)
        return {"run_id": "fake", "emails_fetched": 0, "emails_relevant": 0, "applications_created": 0, "events_created": 0}

    client.app.dependency_overrides[get_full_audit] = lambda: fake_full_audit

    response = client.post("/api/sync/full-audit")
    assert response.status_code == 202
    assert response.json() == {"status": "started"}

    assert _wait_until(lambda: len(calls) == 1)

    status = client.get("/api/sync/status").json()
    assert status["in_progress"] is True
    assert status["current_run_type"] == "full_audit"

    hold.set()
    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])

    final_status = client.get("/api/sync/status").json()
    assert final_status["current_run_type"] is None


def test_full_audit_and_normal_sync_share_the_same_lock(client):
    _reset_state()
    hold = threading.Event()

    def fake_full_audit(settings):
        hold.wait(timeout=5)
        return {"run_id": "fake", "emails_fetched": 0, "emails_relevant": 0, "applications_created": 0, "events_created": 0}

    client.app.dependency_overrides[get_full_audit] = lambda: fake_full_audit

    response = client.post("/api/sync/full-audit")
    assert response.status_code == 202
    assert _wait_until(lambda: client.get("/api/sync/status").json()["in_progress"] is True)

    # A normal sync while a full audit is in progress must be rejected too -
    # they share one lock, never run concurrently regardless of kind.
    conflict = client.post("/api/sync")
    assert conflict.status_code == 409

    hold.set()
    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])


def test_normal_sync_and_full_audit_share_the_same_lock_other_direction(client):
    """Inverse of the above: a full audit attempted while a normal sync is
    already running must be rejected too."""
    _reset_state()
    hold = threading.Event()

    def fake_run_sync(settings):
        hold.wait(timeout=5)
        return {"run_id": "fake", "emails_fetched": 0, "emails_relevant": 0, "applications_created": 0, "events_created": 0}

    client.app.dependency_overrides[get_run_sync] = lambda: fake_run_sync

    response = client.post("/api/sync")
    assert response.status_code == 202
    assert _wait_until(lambda: client.get("/api/sync/status").json()["in_progress"] is True)

    conflict = client.post("/api/sync/full-audit")
    assert conflict.status_code == 409

    hold.set()
    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])


def test_stop_returns_409_when_nothing_in_progress(client):
    _reset_state()
    response = client.post("/api/sync/stop")
    assert response.status_code == 409


def test_stop_sets_cancel_flag_that_a_running_sync_actually_observes(client):
    """Integration check that POST /stop's request_cancel() call is visible
    to the same run_control module the pipeline itself checks - not just
    that the endpoint flips its own local state."""
    from applysync.run_control import is_cancel_requested

    _reset_state()
    release = threading.Event()
    observed_cancel = threading.Event()

    def fake_run_sync(settings):
        # Simulates process_emails's own between-email loop: poll
        # is_cancel_requested() until it flips, standing in for "still has
        # more emails to process". Blocks on `release` afterward (not a
        # timing accident) so the test has a real window to assert
        # status["stopping"] is True before the run actually finishes -
        # without this, the fake returning immediately after observing the
        # cancel races the very next status poll.
        while not is_cancel_requested():
            time.sleep(0.01)
        observed_cancel.set()
        release.wait(timeout=5)
        return {"run_id": "fake", "emails_fetched": 1, "emails_relevant": 0, "applications_created": 0, "events_created": 0}

    client.app.dependency_overrides[get_run_sync] = lambda: fake_run_sync

    response = client.post("/api/sync")
    assert response.status_code == 202
    assert _wait_until(lambda: client.get("/api/sync/status").json()["in_progress"] is True)

    status_before_stop = client.get("/api/sync/status").json()
    assert status_before_stop["stopping"] is False

    stop_response = client.post("/api/sync/stop")
    assert stop_response.status_code == 200
    assert stop_response.json() == {"status": "stopping"}

    status_after_stop = client.get("/api/sync/status").json()
    assert status_after_stop["stopping"] is True

    assert _wait_until(observed_cancel.is_set, timeout=2.0)
    # Still in progress and still reporting "stopping" while the fake is
    # deliberately held here - proves the flag doesn't reset until the run
    # actually finishes, not merely once cancellation was observed.
    still_stopping = client.get("/api/sync/status").json()
    assert still_stopping["in_progress"] is True
    assert still_stopping["stopping"] is True

    release.set()
    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])
    final_status = client.get("/api/sync/status").json()
    assert final_status["stopping"] is False


def test_stop_clears_between_runs_so_a_fresh_sync_is_never_born_cancelled(client):
    from applysync.run_control import is_cancel_requested, request_cancel

    _reset_state()
    request_cancel()
    assert is_cancel_requested() is True

    hold = threading.Event()

    def fake_run_sync(settings):
        hold.wait(timeout=5)
        return {"run_id": "fake", "emails_fetched": 0, "emails_relevant": 0, "applications_created": 0, "events_created": 0}

    client.app.dependency_overrides[get_run_sync] = lambda: fake_run_sync

    response = client.post("/api/sync")
    assert response.status_code == 202
    assert _wait_until(lambda: client.get("/api/sync/status").json()["in_progress"] is True)

    assert is_cancel_requested() is False

    hold.set()
    assert _wait_until(lambda: not client.get("/api/sync/status").json()["in_progress"])
