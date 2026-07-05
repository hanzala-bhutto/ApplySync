from datetime import date, datetime

from applysync.db import repository as repo


def test_is_processed_false_until_marked(session):
    assert repo.is_processed(session, "msg-1") is False
    repo.mark_processed(session, "msg-1", classification="relevant", pipeline_run_id="run-1")
    assert repo.is_processed(session, "msg-1") is True


def test_create_application_and_find_matching(session):
    created = repo.create_application(
        session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    found = repo.find_matching_application(
        session, company_name="Acme Corp", job_title="Backend Engineer", platform="linkedin"
    )

    assert found is not None
    assert found.id == created.id


def test_find_matching_application_returns_none_when_no_match(session):
    found = repo.find_matching_application(
        session, company_name="Nope Inc", job_title="Nothing", platform="linkedin"
    )
    assert found is None


def test_add_status_event_updates_application_current_status(session):
    application = repo.create_application(
        session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    repo.add_status_event(
        session,
        application_id=application.id,
        status="interview",
        event_date=datetime(2026, 1, 10),
        source_email_id="msg-2",
    )

    session.refresh(application)
    assert application.current_status == "interview"


def test_stale_applications_finds_old_untouched_applied_rows(session):
    repo.create_application(
        session,
        company_name="OldCo",
        job_title="Role",
        platform="indeed",
        applied_date=date(2020, 1, 1),
        current_status="applied",
    )
    repo.create_application(
        session,
        company_name="RecentCo",
        job_title="Role",
        platform="indeed",
        applied_date=date.today(),
        current_status="applied",
    )

    stale = repo.stale_applications(session, days=14)

    assert {a.company_name for a in stale} == {"OldCo"}


def test_pipeline_run_lifecycle(session):
    repo.create_pipeline_run(session, "run-1")
    finished = repo.finish_pipeline_run(
        session,
        "run-1",
        emails_fetched=10,
        emails_relevant=3,
        applications_created=2,
        events_created=1,
    )
    assert finished.finished_at is not None
    assert finished.emails_fetched == 10
