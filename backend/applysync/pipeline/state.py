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


class DisambiguationVerdict(BaseModel):
    """The disambiguation agent's structured decision for an ambiguous match
    (a candidate exists for the same company+platform but the title doesn't
    match exactly - the documented Nagarro/EGYM gap).

    Flat scalar fields only. Built from the agent's terminal `submit_verdict`
    tool call (see research/disambiguate.py), NOT with_structured_output -
    which returns empty on this model once a schema has any list field (the
    same hard-won constraint as CompanyProfileResult). Native tool-calling
    with scalar args is reliable on this model (verified), so the agent's
    tools, including this verdict, use scalar args throughout.
    Maps onto MatchDecision in the disambiguate node:
      same_application      -> update_existing (application_id required)
      different_application -> new_application
      duplicate             -> duplicate_skip  (application_id required)
    """

    decision: Literal["same_application", "different_application", "duplicate"] = Field(
        description="same_application: this email is a status update for an existing "
        "application. different_application: a genuinely separate application at the "
        "same company. duplicate: a redundant re-confirmation of an existing "
        "application that should NOT create a new row or a new status event."
    )
    matched_application_id: int | None = Field(
        default=None,
        description="The id of the existing application this matches. Required when "
        "decision is same_application or duplicate; null for different_application.",
    )
    reasoning: str = Field(
        description="One or two sentences explaining the decision, stored so a human "
        "can see why the pipeline deduped or split these applications."
    )


class RelevanceOnlyResult(BaseModel):
    """Structured-output schema for scrutinize_relevance's cheap ambiguous-case
    LLM call - just a bool, not the full extraction schema, since this call
    only needs to resolve a heuristic "maybe" into pass/reject.
    """

    is_relevant: bool = Field(
        description="True if this email is about a job application the user personally "
        "submitted (confirmation, status update, interview, rejection, offer). False for "
        "job alert digests, newsletters, or job recommendation emails."
    )


class EmailState(TypedDict, total=False):
    """Per-email pipeline state. One EmailState flows through
    scrutinize_relevance -> classify_and_extract -> match_existing_application
    -> upsert_db (or short-circuits to mark_scrutiny_rejected / mark_irrelevant /
    mark_extraction_failed).
    """

    email: RawEmail
    platform_hint: str | None
    scrutiny: Literal["pass", "reject"]
    classification: Literal["relevant", "irrelevant"]
    extracted: JobApplicationEvent | None
    # Set by match_existing_application ONLY for the ambiguous case (a
    # same-company+platform candidate exists but no exact title match): the ids
    # the disambiguate node reasons over. Left unset (and match left None) is
    # the routing signal that disambiguation is needed.
    candidate_ids: list[int] | None
    match: MatchDecision | None
    # The disambiguation agent's rationale, when this email went through it -
    # stored on the resulting status event's notes so a human can see why the
    # pipeline deduped or split the applications.
    disambiguation_reasoning: str | None
    error: str | None
