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
    status: Literal["applied", "viewed", "assessment", "interview", "rejected", "offer", "other"]
    job_url: str | None = Field(default=None, description="Link to the application or posting, if present")
    location: str | None = Field(default=None, description="Job location, if mentioned")
    salary_text: str | None = Field(default=None, description="Salary/compensation text, if mentioned")


class ClassifyAndExtractResult(BaseModel):
    """Structured-output schema for the merged classify+extract call. One
    call instead of two (classify_relevant then extract_structured_data)
    roughly halves per-email LLM latency, which mattered in practice: at
    ~7s/call, two sequential calls put every email over a 10s target before
    anything even went wrong.
    """

    is_relevant: bool = Field(
        description="True if this is a genuine job-application confirmation or status "
        "update (applied, viewed, interview invite, rejection, offer). False for job alert "
        "digests, newsletters, marketing, or anything unrelated to an application you "
        "personally submitted."
    )
    company_name: str | None = Field(default=None, description="Only if is_relevant")
    job_title: str | None = Field(
        default=None,
        description="Job title applied for, only if is_relevant. Null if the email "
        "genuinely does not mention one, never a placeholder like 'not specified'.",
    )
    status: Literal["applied", "viewed", "assessment", "interview", "rejected", "offer", "other"] | None = Field(
        default=None, description="Only if is_relevant"
    )
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
