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
    source_email_id: str
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
