from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Application(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "company_name", "job_title", "platform", "applied_date", name="uq_application_identity"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_name: str
    job_title: str
    platform: str
    job_url: str | None = None
    location: str | None = None
    salary_text: str | None = None
    applied_date: date
    current_status: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class StatusEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id")
    status: str
    event_date: datetime
    # Nullable: manual corrections made from the dashboard (drag-and-drop,
    # inline edit) are status events too, but they don't originate from an
    # email.
    source_email_id: str | None = None
    raw_extract_json: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ProcessedEmail(SQLModel, table=True):
    """Idempotency guard: once a Gmail message id lands here, fetch_emails
    excludes it from every future run, regardless of what pipeline_run
    processed it or whether the email turned out relevant.
    """

    message_id: str = Field(primary_key=True)
    processed_at: datetime = Field(default_factory=_utcnow)
    classification: str
    pipeline_run_id: str


class PipelineRun(SQLModel, table=True):
    id: str = Field(primary_key=True)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    emails_fetched: int = 0
    emails_relevant: int = 0
    applications_created: int = 0
    events_created: int = 0
    errors: str | None = None
    # Incremental progress fields, populated as the run streams through the
    # graph rather than only once the whole run finishes - power a staged
    # sync-progress view. All additive/nullable-or-defaulted: see
    # db/init_db.py's migration for how these are added to an existing
    # database file without a full migration tool.
    emails_total: int | None = None
    emails_scrutinized: int = 0
    emails_extracted: int = 0
    emails_written: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)
    # Only meaningful for run_type="full_audit" (always 0 for an incremental
    # sync, which never creates suggestions) - a full audit never auto-applies
    # anything, so this is the number that actually answers "did this run
    # find anything worth looking at", not applications_created/events_created
    # which are always 0 for a full audit by design.
    suggestions_created: int = 0
    # "incremental" (normal Sync Now / applysync sync) or "full_audit" (see
    # ReviewSuggestion below) - lets the /sync page's shared progress-bar UI
    # distinguish which kind of run is in flight. Older rows may still hold
    # the pre-rename value "full_scan" (see docs/feasibility/
    # full-audit-rename.md) - display code should treat both as equivalent.
    run_type: str = "incremental"


class CompanyProfile(SQLModel, table=True):
    """Web-researched profile for a company, cached and shared across every
    application at that company. Deliberately a SEPARATE table from
    Application: this is web-sourced ("the internet suggested this"), never to
    be mixed with the email-extracted facts on Application ("the company told
    me this"). source_urls is kept so a human can verify every claim.
    """

    # Lowercased company name as the cache key, so "EGYM" and "egym" share one
    # profile. The display name the research ran against is kept separately.
    company_key: str = Field(primary_key=True)
    display_name: str
    summary: str | None = None
    industry: str | None = None
    company_size: str | None = None
    headquarters: str | None = None
    website: str | None = None
    # Free-text note on recent news (kept as plain text, not a list: the model
    # that produces it reliably returns text but returns empty for list-typed
    # structured output - see research/company.py).
    recent_news: str | None = None
    # JSON-encoded list of the source result URLs the synthesis was grounded
    # in, so a human can verify every claim (store-JSON-as-str pattern, same as
    # ReviewSuggestion's *_extract_json).
    source_urls_json: str | None = None
    researched_at: datetime = Field(default_factory=_utcnow)


class ReviewSuggestion(SQLModel, table=True):
    """A full-audit run's proposed change, never auto-applied: re-running
    today's pipeline against an already-processed email can disagree with
    what's currently stored (an improved prompt, a broadened keyword filter,
    or the scrutiny node's rare false positives/negatives), so the new
    result is queued here for the user to approve or reject rather than
    silently overwriting real data.
    """

    id: int | None = Field(default=None, primary_key=True)
    message_id: str
    # Existing application this relates to, if any (None for a suggested
    # brand-new application that doesn't match anything on record).
    application_id: int | None = Field(default=None, foreign_key="application.id")
    # "new_application" | "update_existing" | "reclassify_irrelevant" (full audit)
    # | "merge_into" (confidence-routed merge: the pipeline auto-created a new
    # application but the disambiguation agent thought, with low confidence, it
    # belonged to application_id - approving collapses the new row into that one)
    action: str
    previous_classification: str
    suggested_classification: str
    # JSON snapshots for diff display on the review page; None where not
    # applicable (e.g. previous_extract_json is None if the email was never
    # previously relevant, suggested_extract_json is None for
    # reclassify_irrelevant since there's nothing new to extract).
    previous_extract_json: str | None = None
    suggested_extract_json: str | None = None
    status: str = "pending"
    # The disambiguation agent's confidence ("high"/"medium"/"low") for a
    # merge_into suggestion, so a human can see how unsure the pipeline was.
    # None for full-audit suggestions, which don't come from the agent.
    confidence: str | None = None
    pipeline_run_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    reviewed_at: datetime | None = None
