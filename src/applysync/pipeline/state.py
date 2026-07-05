from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from applysync.gmail.models import RawEmail


class JobApplicationEvent(BaseModel):
    """Structured-output schema for extract_structured_data. Every field
    besides status is nullable, since a single email rarely mentions all of
    them, missing fields are not extraction failures.
    """

    company_name: str = Field(description="Company the application was sent to")
    job_title: str | None = Field(
        default=None,
        description="Job title applied for. Null if the email genuinely does not "
        "mention one, never a placeholder like 'not specified' or 'unknown'.",
    )
    status: Literal["applied", "viewed", "interview", "rejected", "offer", "other"]
    job_url: str | None = Field(default=None, description="Link to the application or posting, if present")
    location: str | None = Field(default=None, description="Job location, if mentioned")
    salary_text: str | None = Field(default=None, description="Salary/compensation text, if mentioned")


class MatchDecision(BaseModel):
    action: Literal["new_application", "update_existing", "duplicate_skip"]
    application_id: int | None = None


class EmailState(TypedDict, total=False):
    """Per-email pipeline state. One EmailState flows through
    classify_relevant -> extract_structured_data -> match_existing_application
    -> upsert_db (or short-circuits to mark_irrelevant / mark_extraction_failed).
    """

    email: RawEmail
    platform_hint: str | None
    classification: Literal["relevant", "irrelevant"]
    extracted: JobApplicationEvent | None
    match: MatchDecision | None
    error: str | None
