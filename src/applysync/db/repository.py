from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from applysync.db.models import Application, PipelineRun, ProcessedEmail, StatusEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_processed(session: Session, message_id: str) -> bool:
    return session.get(ProcessedEmail, message_id) is not None


def mark_processed(
    session: Session, message_id: str, classification: str, pipeline_run_id: str
) -> None:
    session.add(
        ProcessedEmail(
            message_id=message_id,
            classification=classification,
            pipeline_run_id=pipeline_run_id,
        )
    )
    session.commit()


def find_matching_application(
    session: Session, company_name: str, job_title: str, platform: str
) -> Application | None:
    """Heuristic-first match: exact company + title + platform. Ambiguous
    cases (near-duplicates, renamed titles) are the LLM-fallback's job in the
    match_existing_application pipeline node, not this function.
    """
    statement = select(Application).where(
        Application.company_name == company_name,
        Application.job_title == job_title,
        Application.platform == platform,
    )
    return session.exec(statement).first()


def create_application(
    session: Session,
    *,
    company_name: str,
    job_title: str,
    platform: str,
    applied_date: date,
    current_status: str,
    job_url: str | None = None,
    location: str | None = None,
    salary_text: str | None = None,
) -> Application:
    application = Application(
        company_name=company_name,
        job_title=job_title,
        platform=platform,
        applied_date=applied_date,
        current_status=current_status,
        job_url=job_url,
        location=location,
        salary_text=salary_text,
    )
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


def add_status_event(
    session: Session,
    *,
    application_id: int,
    status: str,
    event_date: datetime,
    source_email_id: str,
    raw_extract_json: str | None = None,
    notes: str | None = None,
) -> StatusEvent:
    event = StatusEvent(
        application_id=application_id,
        status=status,
        event_date=event_date,
        source_email_id=source_email_id,
        raw_extract_json=raw_extract_json,
        notes=notes,
    )
    session.add(event)

    application = session.get(Application, application_id)
    if application is not None:
        application.current_status = status
        application.updated_at = _utcnow()
        session.add(application)

    session.commit()
    session.refresh(event)
    return event


STATUS_ORDER = ["applied", "viewed", "interview", "offer", "rejected", "other"]


def applications_by_status(session: Session) -> dict[str, list[Application]]:
    """Groups every application by current_status for the dashboard's kanban
    board. Statuses in STATUS_ORDER always appear as a column even when
    empty; any status not in that list (there shouldn't be one, but nothing
    enforces it at the DB layer) still gets its own column rather than being
    silently dropped.
    """
    board: dict[str, list[Application]] = {status: [] for status in STATUS_ORDER}
    for application in session.exec(select(Application)).all():
        board.setdefault(application.current_status, []).append(application)
    return board


def platform_breakdown(session: Session) -> list[dict]:
    """Per-platform application counts and response rate (anything past
    'applied' counts as a response) for the dashboard's breakdown view.
    """
    breakdown: dict[str, dict] = {}
    for application in session.exec(select(Application)).all():
        entry = breakdown.setdefault(
            application.platform, {"platform": application.platform, "total": 0, "responded": 0}
        )
        entry["total"] += 1
        if application.current_status != "applied":
            entry["responded"] += 1
    return sorted(breakdown.values(), key=lambda e: -e["total"])


def application_timeline(session: Session, application_id: int) -> list[StatusEvent]:
    statement = (
        select(StatusEvent)
        .where(StatusEvent.application_id == application_id)
        .order_by(StatusEvent.event_date)
    )
    return list(session.exec(statement).all())


def stale_applications(session: Session, *, days: int = 14) -> list[Application]:
    """Applications still in 'applied' status with no update in `days` days,
    used for the dashboard's follow-up reminders (a reporting query, not a
    pipeline node).
    """
    cutoff = date.today() - timedelta(days=days)
    statement = select(Application).where(
        Application.current_status == "applied",
        Application.applied_date < cutoff,
    )
    return list(session.exec(statement).all())


def create_pipeline_run(session: Session, run_id: str) -> PipelineRun:
    run = PipelineRun(id=run_id)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def finish_pipeline_run(
    session: Session,
    run_id: str,
    *,
    emails_fetched: int,
    emails_relevant: int,
    applications_created: int,
    events_created: int,
    errors: str | None = None,
) -> PipelineRun:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise ValueError(f"No pipeline_run with id {run_id!r}")
    run.finished_at = _utcnow()
    run.emails_fetched = emails_fetched
    run.emails_relevant = emails_relevant
    run.applications_created = applications_created
    run.events_created = events_created
    run.errors = errors
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
