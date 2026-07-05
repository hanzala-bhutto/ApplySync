from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from applysync.db.models import Application, PipelineRun, ProcessedEmail, StatusEvent

# Common legal-entity suffixes that show up inconsistently across emails for
# the same real company (e.g. "EGYM" vs "EGYM SE" - two confirmation emails
# for one application, extracted with different suffixes). Stripped only for
# the match lookup below, never for what gets stored or displayed.
_LEGAL_SUFFIXES = {
    "se", "gmbh", "inc", "ltd", "ag", "co", "llc", "corp", "corporation", "limited", "plc",
}


def _normalize_for_matching(name: str) -> str:
    normalized = re.sub(r"[.,]", "", name.lower().strip())
    words = normalized.split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


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
    """Heuristic-first match: company + title + platform, normalized (case,
    whitespace, legal suffixes) so e.g. "EGYM" and "EGYM SE" from two emails
    for the same application still match. Remaining ambiguous cases (real
    near-duplicates, renamed titles) are the LLM-fallback's job in the
    match_existing_application pipeline node, not this function.

    Normalization happens in Python, not SQL, since matching now scans
    candidates for the platform rather than doing an exact-equality WHERE -
    fine at this project's scale (a personal application tracker, not a
    high-volume table).
    """
    target_company = _normalize_for_matching(company_name)
    target_title = _normalize_for_matching(job_title)

    candidates = session.exec(select(Application).where(Application.platform == platform)).all()
    for candidate in candidates:
        if (
            _normalize_for_matching(candidate.company_name) == target_company
            and _normalize_for_matching(candidate.job_title) == target_title
        ):
            return candidate
    return None


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
    source_email_id: str | None = None,
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


def get_application(session: Session, application_id: int) -> Application | None:
    return session.get(Application, application_id)


def set_manual_status(session: Session, application_id: int, status: str) -> Application | None:
    """Drag-and-drop correction from the dashboard: writes a real status
    event (source_email_id=None, since it didn't come from an email) rather
    than silently overwriting current_status, so the timeline still shows
    that a correction happened and when.
    """
    application = session.get(Application, application_id)
    if application is None:
        return None
    add_status_event(session, application_id=application_id, status=status, event_date=_utcnow(), notes="Manually corrected from the dashboard")
    session.refresh(application)
    return application


def update_application_fields(
    session: Session,
    application_id: int,
    *,
    company_name: str | None = None,
    job_title: str | None = None,
    platform: str | None = None,
) -> Application | None:
    """Inline-edit correction for extracted fields the LLM got wrong. Only
    overwrites fields actually passed in, so a partial edit (e.g. just
    platform) doesn't blank out the others.
    """
    application = session.get(Application, application_id)
    if application is None:
        return None
    if company_name is not None:
        application.company_name = company_name
    if job_title is not None:
        application.job_title = job_title
    if platform is not None:
        application.platform = platform
    application.updated_at = _utcnow()
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


def delete_application(session: Session, application_id: int) -> bool:
    """Removes an application and its status events entirely. For the case
    where reprocessing reveals it should never have been tracked at all
    (e.g. an "incomplete application" reminder email misclassified as a real
    submission), not for normal corrections - those are update_application_fields
    / set_manual_status.
    """
    application = session.get(Application, application_id)
    if application is None:
        return False
    events = session.exec(select(StatusEvent).where(StatusEvent.application_id == application_id)).all()
    for event in events:
        session.delete(event)
    session.delete(application)
    session.commit()
    return True


STATUS_ORDER = ["applied", "viewed", "assessment", "interview", "offer", "rejected", "other"]


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


def last_successful_run_started_at(session: Session) -> datetime | None:
    """Bounds the Gmail query for subsequent syncs (build_search_query's
    `after` param) so a caught-up inbox doesn't re-scan its entire history
    every run. Uses started_at (not finished_at) of the last run to leave a
    small overlap rather than a gap; processed_emails already dedupes
    anything re-fetched in that overlap.
    """
    statement = (
        select(PipelineRun)
        .where(PipelineRun.finished_at.is_not(None))
        .order_by(PipelineRun.started_at.desc())
    )
    last_run = session.exec(statement).first()
    return last_run.started_at if last_run else None


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
