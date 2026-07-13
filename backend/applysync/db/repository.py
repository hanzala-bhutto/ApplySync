from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from applysync.db.models import (
    Application,
    CompanyProfile,
    PipelineRun,
    ProcessedEmail,
    ReviewSuggestion,
    StatusEvent,
)

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


# Below this score, two normalized company names are treated as unrelated for
# the character-level typo check. Chosen from a real case, not a generic
# default: a one-character typo on a short name ("egym" vs "egyg") only scores
# ~75 on any string-distance metric, since a single edit is a large fraction
# of a 4-letter word - a higher bar would miss the exact case this feature
# exists for.
_FUZZY_COMPANY_THRESHOLD = 75


def _is_company_token_subset(a: str, b: str) -> bool:
    """True if the shorter name's words are all present in the longer name's
    words, e.g. "galvany" vs "galvany energy" (a word added/removed). This is
    a strict subset check, not fuzz.token_set_ratio's partial-overlap score:
    token_set_ratio alone produced a real false positive found by running the
    cleanup script against the live database ("Cloud&Heat Technologies GmbH"
    vs "Nash Technologies" scored 82.8, purely from sharing the generic word
    "technologies", with no other overlap). Requiring every word of the
    shorter name to appear, not just a partial token match, rejects that case
    while still matching the real Galvany one.
    """
    tokens_a, tokens_b = set(a.split()), set(b.split())
    shorter, longer = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    return bool(shorter) and shorter <= longer


def _company_names_match(a: str, b: str) -> bool:
    """Covers both real fragmentation classes found in this project's data: a
    character-level typo (fuzz.ratio above threshold, e.g. "egym"/"egyg") or a
    word added/removed (strict token subset, e.g. "galvany"/"galvany energy").
    A fuzzy hit is NEVER auto-merged in the live pipeline: it only expands the
    candidate set match_existing_application hands to the disambiguation agent
    (see make_match_node), so a false positive here costs one extra agent
    call, not a wrong merge - the one-off cleanup script still requires a
    human to review the printed plan before --apply, too.
    """
    return fuzz.ratio(a, b) >= _FUZZY_COMPANY_THRESHOLD or _is_company_token_subset(a, b)


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


def find_exact_company_applications(
    session: Session, *, company_name: str
) -> list[Application]:
    """Every application whose normalized company name (case, whitespace, legal
    suffixes stripped) is EXACTLY equal to the given one, regardless of
    job_title OR platform. Used only by find_matching_application, which must
    stay exact-company: a fuzzy company hit is always routed to the
    disambiguation agent (see find_candidate_applications), even when the title
    also matches exactly, so this function must never surface those.
    """
    target_company = _normalize_for_matching(company_name)
    candidates = session.exec(select(Application).order_by(Application.id)).all()
    return [c for c in candidates if _normalize_for_matching(c.company_name) == target_company]


def find_candidate_applications(
    session: Session, *, company_name: str
) -> list[Application]:
    """Every application for the same-or-similar company (normalized, then
    fuzzy-matched - see _company_fuzzy_score), regardless of job_title OR
    platform. This is the candidate set the disambiguation agent reasons over:
    either the exact-title match in find_matching_application missed because
    the title differs or is missing (the documented missing-title-vs-different-
    title gap), or because the company itself is only a fuzzy hit (a typo like
    "EGYM"/"EGYG" or a word added/removed like "Galvany"/"Galvany Energy") and
    must be agent-confirmed rather than auto-merged regardless of title.
    Platform is deliberately NOT a filter (see find_matching_application): it
    is a per-email attribution label, not identity, so candidates must span
    platforms.

    Normalization/fuzzy-scoring happens in Python, not SQL - fine at this
    project's scale (a personal application tracker, not a high-volume table).
    Ordered by id so the oldest row is considered first, deterministically.
    """
    target_company = _normalize_for_matching(company_name)
    candidates = session.exec(select(Application).order_by(Application.id)).all()
    return [
        c for c in candidates
        if _company_names_match(target_company, _normalize_for_matching(c.company_name))
    ]


def find_matching_application(
    session: Session, company_name: str, job_title: str
) -> Application | None:
    """Heuristic match on EXACT normalized company + EXACT title only. Platform
    is deliberately NOT part of application identity: it is a per-email
    attribution label guessed from the sender (guess_platform), so the SAME
    real application scatters across platform values as different senders
    email about it - the ATS vendor vs the company's own domain. Seen for
    real: a Galvany application whose interview updates came in as platform
    "other" and whose rejection came via ashbyhq.com (platform "ashby") landed
    in two separate rows. Matching on company+title collapses those onto one
    application; the platform column is still stored and displayed, just never
    used to decide identity.

    Normalization (case, whitespace, legal suffixes) still applies so "EGYM"
    and "EGYM SE" match. Deliberately uses find_exact_company_applications, not
    the fuzzy find_candidate_applications: a fuzzy-only company hit must always
    go through the disambiguation agent (see make_match_node), even when the
    title matches exactly too, so it can never resolve here. Remaining
    ambiguous cases (fuzzy company, or a missing/different title for an exact-
    company match) are the disambiguation agent's job (see
    find_candidate_applications), not this function. The oldest matching row
    wins, deterministically.
    """
    target_title = _normalize_for_matching(job_title)
    for candidate in find_exact_company_applications(session, company_name=company_name):
        if _normalize_for_matching(candidate.job_title) == target_title:
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


def company_key(display_name: str) -> str:
    """Cache key for a company profile: lowercased + whitespace-collapsed so
    "EGYM" and "egym " resolve to one cached profile. Kept simpler than the
    match-normalization above (no legal-suffix stripping) on purpose - a
    profile for "EGYM SE" and one for "EGYM" are close enough to share, but we
    don't want to over-merge distinct companies for a research lookup."""
    return " ".join(display_name.lower().split())


def get_company_profile(session: Session, display_name: str) -> CompanyProfile | None:
    return session.get(CompanyProfile, company_key(display_name))


def upsert_company_profile(
    session: Session,
    *,
    display_name: str,
    summary: str | None,
    industry: str | None,
    company_size: str | None,
    headquarters: str | None,
    website: str | None,
    recent_news: str | None,
    source_urls: list[str] | None,
) -> CompanyProfile:
    """Insert or replace the cached web-research profile for a company. Replace
    (not append) because a refresh should reflect the latest web state, not
    accumulate stale rows."""
    key = company_key(display_name)
    profile = session.get(CompanyProfile, key)
    if profile is None:
        profile = CompanyProfile(company_key=key, display_name=display_name)

    profile.display_name = display_name
    profile.summary = summary
    profile.industry = industry
    profile.company_size = company_size
    profile.headquarters = headquarters
    profile.website = website
    profile.recent_news = recent_news
    profile.source_urls_json = json.dumps(source_urls) if source_urls else None
    profile.researched_at = _utcnow()

    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


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


class ApplicationIdentityConflict(RuntimeError):
    """Updating an application's identity fields would collide with the
    UNIQUE(company_name, job_title, platform, applied_date) of a *different*
    existing application - i.e. the edit/reprocess would make this application a
    duplicate of one already on record. Callers surface a clear 409 instead of
    letting the raw sqlite IntegrityError bubble up as a 500."""

    def __init__(self, message: str, *, existing_id: int | None = None):
        super().__init__(message)
        self.existing_id = existing_id


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

    Raises ApplicationIdentityConflict if the new identity tuple collides with a
    different existing application (e.g. reprocessing #250 re-extracts a company
    that already has an identical row), rather than letting the UNIQUE
    constraint raise a raw IntegrityError.
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
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # rollback reverts the in-memory edits, so application now reflects the
        # stored values again; recompute the intended identity to name the
        # existing row the user collided with.
        intended_company = company_name if company_name is not None else application.company_name
        intended_title = job_title if job_title is not None else application.job_title
        intended_platform = platform if platform is not None else application.platform
        existing = session.exec(
            select(Application).where(
                Application.company_name == intended_company,
                Application.job_title == intended_title,
                Application.platform == intended_platform,
                Application.applied_date == application.applied_date,
                Application.id != application_id,
            )
        ).first()
        raise ApplicationIdentityConflict(
            "an application with the same company, title, platform and applied "
            "date already exists",
            existing_id=existing.id if existing else None,
        ) from exc
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


STATUS_ORDER = ["applied", "viewed", "assessment", "interview", "offer", "declined", "rejected", "other"]


def filtered_applications(
    session: Session,
    *,
    year: int | None = None,
    platform: str | None = None,
    company: str | None = None,
    status: str | None = None,
) -> list[Application]:
    """Single source of truth for the dashboard's filter bar. Callers derive
    the board/breakdown views from this one filtered list rather than each
    re-querying with their own filter logic.
    """
    statement = select(Application)
    if platform:
        statement = statement.where(Application.platform == platform)
    if status:
        statement = statement.where(Application.current_status == status)
    applications = list(session.exec(statement).all())
    if year:
        applications = [a for a in applications if a.applied_date.year == year]
    if company:
        needle = company.strip().lower()
        applications = [a for a in applications if needle in a.company_name.lower()]
    return applications


def filter_options(session: Session) -> dict:
    """Distinct values to populate the dashboard's filter dropdowns."""
    applications = session.exec(select(Application)).all()
    return {
        "years": sorted({a.applied_date.year for a in applications}, reverse=True),
        "platforms": sorted({a.platform for a in applications}),
        "statuses": STATUS_ORDER,
    }


def applications_by_status(applications: list[Application]) -> dict[str, list[Application]]:
    """Groups applications by current_status for the dashboard's kanban
    board. Statuses in STATUS_ORDER always appear as a column even when
    empty; any status not in that list (there shouldn't be one, but nothing
    enforces it at the DB layer) still gets its own column rather than being
    silently dropped.
    """
    board: dict[str, list[Application]] = {status: [] for status in STATUS_ORDER}
    for application in applications:
        board.setdefault(application.current_status, []).append(application)
    return board


def platform_breakdown(applications: list[Application]) -> list[dict]:
    """Per-platform application counts and response rate (anything past
    'applied' counts as a response) for the dashboard's breakdown view.
    """
    breakdown: dict[str, dict] = {}
    for application in applications:
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


def get_status_event(session: Session, event_id: int) -> StatusEvent | None:
    return session.get(StatusEvent, event_id)


def stale_applications(session: Session, *, days: int = 14) -> list[Application]:
    """Applications still in 'applied' status with no update in `days` days,
    used for the dashboard's follow-up reminders (a reporting query, not a
    pipeline node). Ordered oldest-first (most overdue) so callers that slice
    a preview (e.g. the dashboard) get the most urgent ones, not an arbitrary
    subset.
    """
    cutoff = date.today() - timedelta(days=days)
    statement = (
        select(Application)
        .where(
            Application.current_status == "applied",
            Application.applied_date < cutoff,
        )
        .order_by(Application.applied_date.asc())
    )
    return list(session.exec(statement).all())


def stale_applications_count(session: Session, *, days: int = 14) -> int:
    """Total count behind `stale_applications_page`, for pagination controls."""
    cutoff = date.today() - timedelta(days=days)
    statement = select(func.count()).select_from(Application).where(
        Application.current_status == "applied",
        Application.applied_date < cutoff,
    )
    return session.exec(statement).one()


def stale_applications_page(
    session: Session, *, days: int = 14, offset: int = 0, limit: int = 20
) -> list[Application]:
    """DB-paginated version of `stale_applications`, for the dedicated
    Reminders page - unlike the dashboard's bounded preview, this needs to
    stay cheap even with thousands of stale rows.
    """
    cutoff = date.today() - timedelta(days=days)
    statement = (
        select(Application)
        .where(
            Application.current_status == "applied",
            Application.applied_date < cutoff,
        )
        .order_by(Application.applied_date.asc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


def create_pipeline_run(session: Session, run_id: str, *, run_type: str = "incremental") -> PipelineRun:
    run = PipelineRun(id=run_id, run_type=run_type)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def get_latest_pipeline_run(session: Session) -> PipelineRun | None:
    """Most recent run regardless of whether it finished, so the dashboard's
    sync status can reflect one still in progress, not just completed ones.
    """
    statement = select(PipelineRun).order_by(PipelineRun.started_at.desc())
    return session.exec(statement).first()


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


def update_pipeline_run_progress(
    session: Session,
    run_id: str,
    *,
    emails_total: int | None = None,
    emails_scrutinized: int | None = None,
    emails_extracted: int | None = None,
    emails_written: int | None = None,
) -> PipelineRun:
    """Incremental progress update, called as the run streams through the
    graph (see pipeline/graph.py's use of compiled.stream(...)) rather than
    only once at the end - powers a staged sync-progress view. Only fields
    actually passed in are overwritten, so a partial update (e.g. just
    emails_scrutinized) doesn't reset the others.
    """
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise ValueError(f"No pipeline_run with id {run_id!r}")
    if emails_total is not None:
        run.emails_total = emails_total
    if emails_scrutinized is not None:
        run.emails_scrutinized = emails_scrutinized
    if emails_extracted is not None:
        run.emails_extracted = emails_extracted
    if emails_written is not None:
        run.emails_written = emails_written
    run.updated_at = _utcnow()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def list_recent_pipeline_runs(session: Session, *, limit: int = 10) -> list[PipelineRun]:
    """Most recent runs, newest first - for the sync history list (#21)."""
    statement = select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)
    return list(session.exec(statement).all())


def finish_pipeline_run(
    session: Session,
    run_id: str,
    *,
    emails_fetched: int,
    emails_relevant: int,
    applications_created: int,
    events_created: int,
    errors: str | None = None,
    suggestions_created: int = 0,
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
    run.suggestions_created = suggestions_created
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def find_application_by_source_email(session: Session, message_id: str) -> Application | None:
    """Whichever application a previously-processed email contributed a
    status event to, if any - used by full_scan to determine prior context
    when re-examining an already-processed message. None means the email
    was never linked to a real application (it was irrelevant, rejected, or
    extraction failed) at the time it was originally processed.
    """
    statement = select(StatusEvent).where(StatusEvent.source_email_id == message_id)
    event = session.exec(statement).first()
    if event is None:
        return None
    return session.get(Application, event.application_id)


def find_status_event_by_source_email(session: Session, message_id: str) -> StatusEvent | None:
    """The specific status event a previously-processed email originally
    created, if any. full_scan compares a fresh re-extraction against THIS
    event's own status, not the application's current current_status -
    current_status reflects whatever the most recent event says, which for
    an application with more than one status transition (e.g. applied ->
    interview -> rejected) would make re-scanning the original "applied"
    email always look like a disagreement even though nothing is wrong.
    """
    statement = select(StatusEvent).where(StatusEvent.source_email_id == message_id)
    return session.exec(statement).first()


def has_pending_suggestion_for_message(session: Session, message_id: str) -> bool:
    """Guards against duplicate suggestions piling up across repeated
    full-scan runs - without this, running a full scan more than once (or
    a scan that partially completes before crashing, since suggestions are
    committed per-email as the loop proceeds) re-flags the same email every
    time instead of recognizing it's already queued for review.
    """
    statement = select(ReviewSuggestion).where(
        ReviewSuggestion.message_id == message_id, ReviewSuggestion.status == "pending"
    )
    return session.exec(statement).first() is not None


def create_review_suggestion(
    session: Session,
    *,
    message_id: str,
    action: str,
    previous_classification: str,
    suggested_classification: str,
    pipeline_run_id: str,
    application_id: int | None = None,
    previous_extract_json: str | None = None,
    suggested_extract_json: str | None = None,
) -> ReviewSuggestion:
    suggestion = ReviewSuggestion(
        message_id=message_id,
        application_id=application_id,
        action=action,
        previous_classification=previous_classification,
        suggested_classification=suggested_classification,
        previous_extract_json=previous_extract_json,
        suggested_extract_json=suggested_extract_json,
        pipeline_run_id=pipeline_run_id,
    )
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def list_pending_review_suggestions(session: Session) -> list[ReviewSuggestion]:
    statement = (
        select(ReviewSuggestion)
        .where(ReviewSuggestion.status == "pending")
        .order_by(ReviewSuggestion.created_at)
    )
    return list(session.exec(statement).all())


def approve_review_suggestion(session: Session, suggestion_id: int) -> ReviewSuggestion:
    """Applies the suggested change to the real Application/StatusEvent
    tables (for new_application/update_existing), or simply marks the
    suggestion resolved with no data change (for reclassify_irrelevant -
    automatically deleting real application data from a full scan is too
    risky; the existing "delete application" action on the detail page is
    the manual follow-up if the user agrees it should go).
    """
    suggestion = session.get(ReviewSuggestion, suggestion_id)
    if suggestion is None:
        raise ValueError(f"No review_suggestion with id {suggestion_id!r}")
    if suggestion.status != "pending":
        return suggestion

    if suggestion.action in ("new_application", "update_existing") and suggestion.suggested_extract_json:
        extracted = json.loads(suggestion.suggested_extract_json)
        event_date = _utcnow()
        if suggestion.action == "new_application":
            application = create_application(
                session,
                company_name=extracted["company_name"],
                job_title=extracted["job_title"],
                platform=extracted.get("platform") or "other",
                applied_date=event_date.date(),
                current_status=extracted["status"],
                job_url=extracted.get("job_url"),
                location=extracted.get("location"),
                salary_text=extracted.get("salary_text"),
            )
            add_status_event(
                session,
                application_id=application.id,
                status=extracted["status"],
                event_date=event_date,
                source_email_id=suggestion.message_id,
                raw_extract_json=suggestion.suggested_extract_json,
                notes="Full-scan suggestion, approved",
            )
        else:
            update_application_fields(
                session,
                suggestion.application_id,
                company_name=extracted.get("company_name"),
                job_title=extracted.get("job_title"),
            )
            add_status_event(
                session,
                application_id=suggestion.application_id,
                status=extracted["status"],
                event_date=event_date,
                source_email_id=suggestion.message_id,
                raw_extract_json=suggestion.suggested_extract_json,
                notes="Full-scan suggestion, approved",
            )

    suggestion.status = "approved"
    suggestion.reviewed_at = _utcnow()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def reject_review_suggestion(session: Session, suggestion_id: int) -> ReviewSuggestion:
    suggestion = session.get(ReviewSuggestion, suggestion_id)
    if suggestion is None:
        raise ValueError(f"No review_suggestion with id {suggestion_id!r}")
    suggestion.status = "rejected"
    suggestion.reviewed_at = _utcnow()
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return suggestion


def reject_all_pending_suggestions(session: Session) -> int:
    """Bulk dismiss, for clearing out a backlog in one action (e.g. after a
    bug in an earlier full-scan run flooded the queue with false positives -
    rejecting doesn't touch any Application/StatusEvent data, it only
    resolves the suggestion rows themselves). Returns how many were
    rejected.
    """
    pending = list_pending_review_suggestions(session)
    now = _utcnow()
    for suggestion in pending:
        suggestion.status = "rejected"
        suggestion.reviewed_at = now
        session.add(suggestion)
    session.commit()
    return len(pending)
