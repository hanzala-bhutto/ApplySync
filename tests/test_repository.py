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


def test_find_matching_application_ignores_legal_suffix_differences(session):
    """Regression test for a real bug: two confirmation emails for the same
    EGYM application extracted as "EGYM" and "EGYM SE" respectively, so the
    old exact-match lookup created two application rows instead of one.
    """
    created = repo.create_application(
        session,
        company_name="EGYM",
        job_title="(unspecified role)",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    found = repo.find_matching_application(
        session, company_name="EGYM SE", job_title="(unspecified role)", platform="other"
    )

    assert found is not None
    assert found.id == created.id


def test_find_matching_application_normalizes_case_and_whitespace(session):
    created = repo.create_application(
        session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    found = repo.find_matching_application(
        session, company_name="  acme corp  ", job_title="backend engineer", platform="linkedin"
    )

    assert found is not None
    assert found.id == created.id


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


def test_applications_by_status_groups_and_includes_empty_columns(session):
    repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.create_application(
        session,
        company_name="Beta",
        job_title="Engineer",
        platform="indeed",
        applied_date=date(2026, 1, 1),
        current_status="rejected",
    )

    board = repo.applications_by_status(session)

    assert [a.company_name for a in board["applied"]] == ["Acme"]
    assert [a.company_name for a in board["rejected"]] == ["Beta"]
    assert board["interview"] == []


def test_platform_breakdown_counts_total_and_responded(session):
    repo.create_application(
        session,
        company_name="Acme",
        job_title="A",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.create_application(
        session,
        company_name="Beta",
        job_title="B",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="interview",
    )

    breakdown = repo.platform_breakdown(session)

    assert breakdown == [{"platform": "linkedin", "total": 2, "responded": 1}]


def test_application_timeline_orders_events_by_date(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
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
    repo.add_status_event(
        session,
        application_id=application.id,
        status="applied",
        event_date=datetime(2026, 1, 1),
        source_email_id="msg-1",
    )

    timeline = repo.application_timeline(session, application.id)

    assert [e.source_email_id for e in timeline] == ["msg-1", "msg-2"]


def test_set_manual_status_updates_status_and_adds_source_email_id_none_event(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    updated = repo.set_manual_status(session, application.id, "rejected")

    assert updated.current_status == "rejected"
    timeline = repo.application_timeline(session, application.id)
    assert timeline[-1].source_email_id is None
    assert timeline[-1].status == "rejected"


def test_set_manual_status_returns_none_for_unknown_id(session):
    assert repo.set_manual_status(session, 999, "rejected") is None


def test_update_application_fields_only_overwrites_given_fields(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    updated = repo.update_application_fields(session, application.id, company_name="Acme Corp")

    assert updated.company_name == "Acme Corp"
    assert updated.job_title == "Engineer"
    assert updated.platform == "linkedin"


def test_update_application_fields_returns_none_for_unknown_id(session):
    assert repo.update_application_fields(session, 999, company_name="X") is None


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
