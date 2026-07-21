from datetime import date, datetime, timezone

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
        session, company_name="Acme Corp", job_title="Backend Engineer"
    )

    assert found is not None
    assert found.id == created.id


def test_find_matching_application_returns_none_when_no_match(session):
    found = repo.find_matching_application(
        session, company_name="Nope Inc", job_title="Nothing"
    )
    assert found is None


def test_find_matching_application_matches_across_different_platforms(session):
    """Platform is a per-email attribution label, not identity. The same
    application's confirmation and rejection can arrive via different senders
    (its own domain vs an ATS like Ashby) and get different platform values;
    matching must still collapse them onto one application, not fragment it.
    """
    created = repo.create_application(
        session,
        company_name="Galvany Energy",
        job_title="Backend Engineer",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    found = repo.find_matching_application(
        session, company_name="Galvany Energy", job_title="Backend Engineer"
    )

    assert found is not None
    assert found.id == created.id


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
        session, company_name="EGYM SE", job_title="(unspecified role)"
    )

    assert found is not None
    assert found.id == created.id


def test_find_matching_application_ignores_gender_qualifier_differences(session):
    """Regression test found by the eval harness: the same real posting can
    arrive via different ATS templates with/without a gender/diversity
    qualifier on the title (e.g. "(m/f/d)", "(f/m/x)", "(all genders)"),
    which must not fragment the same application into two rows.
    """
    created = repo.create_application(
        session,
        company_name="Acme",
        job_title="Full Stack AI Engineer",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    found = repo.find_matching_application(
        session, company_name="Acme", job_title="Full Stack AI Engineer (m/f/d)"
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
        session, company_name="  acme corp  ", job_title="backend engineer"
    )

    assert found is not None
    assert found.id == created.id


def test_find_matching_application_does_not_fuzzy_match_company(session):
    """find_matching_application must stay EXACT-company: a fuzzy company hit
    (even with an exact title match) always has to go through the
    disambiguation agent instead of auto-resolving here (see
    make_match_node's routing).
    """
    repo.create_application(
        session,
        company_name="EGYM",
        job_title="Backend Engineer",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    found = repo.find_matching_application(
        session, company_name="EGYG", job_title="Backend Engineer"
    )

    assert found is None


def test_find_candidate_applications_includes_typo_company(session):
    created = repo.create_application(
        session,
        company_name="EGYM",
        job_title="Backend Engineer",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    candidates = repo.find_candidate_applications(session, company_name="EGYG")

    assert [c.id for c in candidates] == [created.id]


def test_find_candidate_applications_includes_word_added_company(session):
    created = repo.create_application(
        session,
        company_name="Galvany",
        job_title="Backend Engineer",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    candidates = repo.find_candidate_applications(session, company_name="Galvany Energy")

    assert [c.id for c in candidates] == [created.id]


def test_find_candidate_applications_excludes_shared_generic_word(session):
    """Regression test for a real false positive found by running the fuzzy
    cleanup script against the live database: "Cloud&Heat Technologies GmbH"
    and "Nash Technologies" are unrelated companies that only share the
    generic word "technologies", but fuzz.token_set_ratio alone scored them
    82.8 (above the old threshold) on that overlap. A strict token-subset
    check (see _is_company_token_subset) correctly rejects this pair since
    "cloudheat"/"nash" aren't shared.
    """
    repo.create_application(
        session,
        company_name="Cloud&Heat Technologies GmbH",
        job_title="(unspecified role)",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    candidates = repo.find_candidate_applications(session, company_name="Nash Technologies")

    assert candidates == []


def test_find_candidate_applications_excludes_unrelated_company(session):
    repo.create_application(
        session,
        company_name="Google",
        job_title="Backend Engineer",
        platform="other",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    candidates = repo.find_candidate_applications(session, company_name="Alphabet")

    assert candidates == []


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


def test_add_status_event_out_of_order_does_not_regress_current_status(session):
    """Regression: Gmail's search API returns results newest-first, not
    chronologically, so a full/historical sync can process an application's
    emails in an arbitrary order within one run. current_status must reflect
    whichever event is latest BY event_date, not whichever happened to be
    added to the DB last - confirmed for real, a full resync left an
    application stuck on "applied" despite a chronologically later
    "rejected" event already on record.
    """
    application = repo.create_application(
        session,
        company_name="Acme Corp",
        job_title="Backend Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )

    repo.add_status_event(
        session, application_id=application.id, status="rejected", event_date=datetime(2026, 1, 20)
    )
    # An older email processed AFTER the newer one (out-of-order batch) must
    # not overwrite the already-recorded later status.
    repo.add_status_event(
        session, application_id=application.id, status="applied", event_date=datetime(2026, 1, 10)
    )

    session.refresh(application)
    assert application.current_status == "rejected"


def test_add_status_event_handles_mixed_aware_and_naive_event_dates(session):
    """event_date values in this codebase are a real mix of timezone-aware
    (parsed from an email's Date header) and naive (_utcnow(), assumed UTC) -
    the latest-event comparison must not crash or misorder across that mix.
    """
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
        event_date=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    repo.add_status_event(
        session, application_id=application.id, status="rejected", event_date=datetime(2026, 1, 20)
    )

    session.refresh(application)
    assert application.current_status == "rejected"


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


def test_filtered_applications_by_year(session):
    repo.create_application(
        session, company_name="Acme", job_title="A", platform="linkedin",
        applied_date=date(2025, 6, 1), current_status="applied",
    )
    repo.create_application(
        session, company_name="Beta", job_title="B", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )

    result = repo.filtered_applications(session, year=2026)

    assert [a.company_name for a in result] == ["Beta"]


def test_filtered_applications_by_platform_and_status(session):
    repo.create_application(
        session, company_name="Acme", job_title="A", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )
    repo.create_application(
        session, company_name="Beta", job_title="B", platform="indeed",
        applied_date=date(2026, 1, 1), current_status="rejected",
    )

    result = repo.filtered_applications(session, platform="linkedin", status="applied")

    assert [a.company_name for a in result] == ["Acme"]


def test_filtered_applications_by_company_substring_case_insensitive(session):
    repo.create_application(
        session, company_name="Acme Corp", job_title="A", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )
    repo.create_application(
        session, company_name="Beta Inc", job_title="B", platform="linkedin",
        applied_date=date(2026, 1, 1), current_status="applied",
    )

    result = repo.filtered_applications(session, company="acme")

    assert [a.company_name for a in result] == ["Acme Corp"]


def test_filter_options_returns_distinct_sorted_values(session):
    repo.create_application(
        session, company_name="Acme", job_title="A", platform="linkedin",
        applied_date=date(2025, 1, 1), current_status="applied",
    )
    repo.create_application(
        session, company_name="Beta", job_title="B", platform="indeed",
        applied_date=date(2026, 1, 1), current_status="rejected",
    )

    options = repo.filter_options(session)

    assert options["years"] == [2026, 2025]
    assert options["platforms"] == ["indeed", "linkedin"]
    assert options["statuses"] == repo.STATUS_ORDER


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

    board = repo.applications_by_status(repo.filtered_applications(session))

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

    breakdown = repo.platform_breakdown(repo.filtered_applications(session))

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


def test_delete_application_removes_application_and_its_events(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.add_status_event(
        session, application_id=application.id, status="applied", event_date=datetime(2026, 1, 1), source_email_id="msg-1"
    )

    assert repo.delete_application(session, application.id) is True

    assert repo.get_application(session, application.id) is None
    assert repo.application_timeline(session, application.id) == []


def test_delete_application_returns_false_for_unknown_id(session):
    assert repo.delete_application(session, 999) is False


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


def test_last_successful_run_started_at_returns_none_when_no_runs(session):
    assert repo.last_successful_run_started_at(session) is None


def test_last_successful_run_started_at_ignores_unfinished_runs(session):
    repo.create_pipeline_run(session, "run-unfinished")
    assert repo.last_successful_run_started_at(session) is None


def test_last_successful_run_started_at_returns_most_recent_finished_run(session):
    run1 = repo.create_pipeline_run(session, "run-1")
    repo.finish_pipeline_run(
        session, "run-1", emails_fetched=1, emails_relevant=1, applications_created=1, events_created=1
    )
    run2 = repo.create_pipeline_run(session, "run-2")
    repo.finish_pipeline_run(
        session, "run-2", emails_fetched=1, emails_relevant=1, applications_created=1, events_created=1
    )

    result = repo.last_successful_run_started_at(session)

    assert result == run2.started_at
    assert result >= run1.started_at


# --- find_application_by_source_email ---


def test_find_application_by_source_email_returns_none_when_never_linked(session):
    assert repo.find_application_by_source_email(session, "msg-unknown") is None


def test_find_application_by_source_email_returns_linked_application(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.add_status_event(
        session, application_id=application.id, status="applied", event_date=datetime(2026, 1, 1), source_email_id="msg-1"
    )

    found = repo.find_application_by_source_email(session, "msg-1")

    assert found is not None
    assert found.id == application.id


def test_find_status_event_by_source_email(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="rejected",
    )
    repo.add_status_event(
        session, application_id=application.id, status="applied", event_date=datetime(2026, 1, 1), source_email_id="msg-1"
    )
    repo.add_status_event(
        session, application_id=application.id, status="rejected", event_date=datetime(2026, 1, 5), source_email_id="msg-2"
    )

    event = repo.find_status_event_by_source_email(session, "msg-1")

    assert event is not None
    assert event.status == "applied"
    assert event.status != application.current_status


def test_has_pending_suggestion_for_message(session):
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    assert repo.has_pending_suggestion_for_message(session, "msg-1") is False

    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-1",
        action="new_application",
        previous_classification="irrelevant",
        suggested_classification="relevant",
        pipeline_run_id="run-1",
    )
    assert repo.has_pending_suggestion_for_message(session, "msg-1") is True

    repo.reject_review_suggestion(session, suggestion.id)
    assert repo.has_pending_suggestion_for_message(session, "msg-1") is False


def test_reject_all_pending_suggestions(session):
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    for message_id in ("msg-1", "msg-2", "msg-3"):
        repo.create_review_suggestion(
            session,
            message_id=message_id,
            action="new_application",
            previous_classification="irrelevant",
            suggested_classification="relevant",
            pipeline_run_id="run-1",
        )

    rejected_count = repo.reject_all_pending_suggestions(session)

    assert rejected_count == 3
    assert repo.list_pending_review_suggestions(session) == []


# --- review suggestions ---


def test_create_and_list_pending_review_suggestions(session):
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    repo.create_review_suggestion(
        session,
        message_id="msg-1",
        action="new_application",
        previous_classification="irrelevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "applied"}',
        pipeline_run_id="run-1",
    )

    pending = repo.list_pending_review_suggestions(session)

    assert len(pending) == 1
    assert pending[0].status == "pending"


def test_approve_new_application_suggestion_creates_application(session):
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-1",
        action="new_application",
        previous_classification="irrelevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "applied", "platform": "linkedin"}',
        pipeline_run_id="run-1",
    )

    approved = repo.approve_review_suggestion(session, suggestion.id)

    assert approved.status == "approved"
    assert approved.reviewed_at is not None
    application = repo.find_matching_application(session, "Acme", "Engineer")
    assert application is not None
    assert application.current_status == "applied"


def test_approve_update_existing_suggestion_adds_status_event(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-1",
        application_id=application.id,
        action="update_existing",
        previous_classification="relevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "rejected", "platform": "linkedin"}',
        pipeline_run_id="run-1",
    )

    repo.approve_review_suggestion(session, suggestion.id)

    session.refresh(application)
    assert application.current_status == "rejected"


def test_reject_review_suggestion_leaves_data_untouched(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-1",
        application_id=application.id,
        action="update_existing",
        previous_classification="relevant",
        suggested_classification="relevant",
        suggested_extract_json='{"company_name": "Acme", "job_title": "Engineer", "status": "rejected", "platform": "linkedin"}',
        pipeline_run_id="run-1",
    )

    rejected = repo.reject_review_suggestion(session, suggestion.id)

    assert rejected.status == "rejected"
    session.refresh(application)
    assert application.current_status == "applied"


def test_approve_reclassify_irrelevant_suggestion_makes_no_data_change(session):
    application = repo.create_application(
        session,
        company_name="Acme",
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )
    repo.create_pipeline_run(session, "run-1", run_type="full_audit")
    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-1",
        application_id=application.id,
        action="reclassify_irrelevant",
        previous_classification="relevant",
        suggested_classification="irrelevant",
        pipeline_run_id="run-1",
    )

    approved = repo.approve_review_suggestion(session, suggestion.id)

    assert approved.status == "approved"
    session.refresh(application)
    assert application.current_status == "applied"

def test_last_successful_run_started_at_excludes_cancelled_runs(session):
    """Regression test for a real bug: a run cancelled partway through a
    large batch still gets finished_at set (so it displays correctly as
    "Stopped" rather than stuck "in progress" forever), but must NOT count
    as the bookmark for the next sync's Gmail query - otherwise stopping a
    500-email run after only 20 processed permanently hides the other 480
    from every future sync, since the bookmark would jump to the cancelled
    run's own (nearly "now") start time.
    """
    good_run = repo.create_pipeline_run(session, "run-good")
    repo.finish_pipeline_run(
        session, "run-good", emails_fetched=1, emails_relevant=1, applications_created=1, events_created=1
    )

    repo.create_pipeline_run(session, "run-cancelled")
    repo.finish_pipeline_run(
        session,
        "run-cancelled",
        emails_fetched=20,
        emails_relevant=5,
        applications_created=5,
        events_created=5,
        errors="cancelled_by_user",
    )

    result = repo.last_successful_run_started_at(session)

    assert result == good_run.started_at


def test_last_successful_run_started_at_excludes_failed_runs_that_still_finished(session):
    """Same exclusion, generalized: ANY run with errors set is excluded, not
    just the specific cancelled_by_user sentinel - a run that finished with
    some other error string must not advance the bookmark either."""
    good_run = repo.create_pipeline_run(session, "run-good")
    repo.finish_pipeline_run(
        session, "run-good", emails_fetched=1, emails_relevant=1, applications_created=1, events_created=1
    )

    repo.create_pipeline_run(session, "run-failed")
    repo.finish_pipeline_run(
        session,
        "run-failed",
        emails_fetched=1,
        emails_relevant=1,
        applications_created=1,
        events_created=1,
        errors="some other failure",
    )

    result = repo.last_successful_run_started_at(session)

    assert result == good_run.started_at


# --- merge_applications + confidence-routed merge approval ---


def _app_with_event(session, *, company, title, platform, applied, status, source_email_id, event_date):
    app = repo.create_application(
        session,
        company_name=company,
        job_title=title,
        platform=platform,
        applied_date=applied,
        current_status=status,
    )
    repo.add_status_event(
        session,
        application_id=app.id,
        status=status,
        event_date=event_date,
        source_email_id=source_email_id,
    )
    return app


def test_merge_applications_collapses_source_into_target(session):
    target = _app_with_event(
        session, company="EGYM", title="Engineer", platform="other", applied=date(2026, 6, 10),
        status="applied", source_email_id="e-target", event_date=datetime(2026, 6, 10, 9, 0, 0),
    )
    source = _app_with_event(
        session, company="EGYM SE", title="Engineer", platform="linkedin", applied=date(2026, 6, 5),
        status="rejected", source_email_id="e-source", event_date=datetime(2026, 6, 20, 9, 0, 0),
    )

    merged = repo.merge_applications(session, source_ids=[source.id], target_id=target.id)

    # Target kept, source gone.
    assert merged.id == target.id
    assert repo.get_application(session, source.id) is None
    # Both events now hang off the target.
    timeline = repo.application_timeline(session, target.id)
    assert len(timeline) == 2
    # Earliest applied_date wins, latest event drives current_status.
    assert merged.applied_date == date(2026, 6, 5)
    assert merged.current_status == "rejected"


def test_merge_applications_noop_when_target_missing_or_self(session):
    app = _app_with_event(
        session, company="Acme", title="Eng", platform="other", applied=date(2026, 1, 1),
        status="applied", source_email_id="e1", event_date=datetime(2026, 1, 1, 9, 0, 0),
    )
    # Self-merge is ignored, target unchanged and still present.
    result = repo.merge_applications(session, source_ids=[app.id], target_id=app.id)
    assert result.id == app.id
    assert repo.get_application(session, app.id) is not None
    # Unknown target returns None.
    assert repo.merge_applications(session, source_ids=[app.id], target_id=99999) is None


def test_approve_merge_into_suggestion_collapses_new_row_into_candidate(session):
    candidate = _app_with_event(
        session, company="Nagarro", title="Backend Engineer", platform="linkedin",
        applied=date(2026, 6, 1), status="applied", source_email_id="seed",
        event_date=datetime(2026, 6, 1, 9, 0, 0),
    )
    # The pipeline auto-created this new row for the ambiguous email (msg-new).
    new_app = _app_with_event(
        session, company="Nagarro", title="(unspecified role)", platform="linkedin",
        applied=date(2026, 6, 15), status="interview", source_email_id="msg-new",
        event_date=datetime(2026, 6, 15, 9, 0, 0),
    )
    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-new",
        action="merge_into",
        application_id=candidate.id,
        previous_classification="relevant",
        suggested_classification="relevant",
        confidence="low",
        pipeline_run_id="run-1",
    )

    approved = repo.approve_review_suggestion(session, suggestion.id)

    assert approved.status == "approved"
    # new_app collapsed into candidate; only candidate remains, with both events.
    assert repo.get_application(session, new_app.id) is None
    timeline = repo.application_timeline(session, candidate.id)
    assert len(timeline) == 2
    assert repo.get_application(session, candidate.id).current_status == "interview"


def test_reject_merge_into_suggestion_keeps_both_rows(session):
    candidate = _app_with_event(
        session, company="Nagarro", title="Backend Engineer", platform="linkedin",
        applied=date(2026, 6, 1), status="applied", source_email_id="seed",
        event_date=datetime(2026, 6, 1, 9, 0, 0),
    )
    new_app = _app_with_event(
        session, company="Nagarro", title="(unspecified role)", platform="linkedin",
        applied=date(2026, 6, 15), status="interview", source_email_id="msg-new",
        event_date=datetime(2026, 6, 15, 9, 0, 0),
    )
    suggestion = repo.create_review_suggestion(
        session,
        message_id="msg-new",
        action="merge_into",
        application_id=candidate.id,
        previous_classification="relevant",
        suggested_classification="relevant",
        confidence="low",
        pipeline_run_id="run-1",
    )

    repo.reject_review_suggestion(session, suggestion.id)

    # Reject is a pure no-op on data: both applications still exist, untouched.
    assert repo.get_application(session, candidate.id) is not None
    assert repo.get_application(session, new_app.id) is not None
