---
name: code-review
description: Project-specific review checklist for ApplySync (idempotency correctness, Gmail credential/PII handling, schema/migration safety, prompt-schema drift). Complements the global /code-review, use this for ApplySync-specific risk areas, use global /code-review for general correctness/simplification review.
---

# ApplySync code review checklist

Run this alongside (not instead of) the global `/code-review` for changes
touching this project. Focus on the risk areas specific to an email-parsing,
LLM-extraction, credential-holding pipeline. General bug-hunting is the
global skill's job.

## Idempotency correctness

- Does every code path that processes an email write to `processed_emails`
  before or atomically with the rest of its side effects? A crash between
  "extracted and upserted" and "marked processed" should not cause a duplicate
  on retry. Check whether the write order/transaction actually guarantees
  this, don't assume.
- Does `match_existing_application` ever create a new `applications` row for
  what should be a `status_events` update? Check the matching logic's
  fallback/no-match branch specifically, that's where duplicates leak in.
- If `langgraph-checkpoint-sqlite` checkpointing changed, confirm
  `processed_emails` is still the actual dedupe guard, checkpointing is
  crash-recovery only and must not become the sole idempotency mechanism.

## Gmail credential / PII handling

- Is `token.json`/`credentials.json` ever logged, printed, or written to a
  path that isn't gitignored?
- Is the Gmail scope still readonly-only anywhere new code touches the API?
- Do logs, error messages, or `raw_extract_json` audit columns capture full
  email bodies unnecessarily? Prefer storing only the fields needed for
  debugging extraction, not the entire raw email, when it contains PII beyond
  what's needed (e.g. unrelated email content pulled in by an overly broad
  Gmail query).
- Are `.env`/`.secrets/` actually excluded by `.gitignore`? Check the literal
  file, don't assume.

## Schema / migration safety

- Does a model change in `db/models.py` have a corresponding migration path
  (or is it additive/safe for existing local SQLite files)? A silent schema
  change that breaks an existing user's local DB is a real regression here,
  since there's no server-side migration safety net.
- Are new columns nullable or defaulted so existing rows don't break?

## Prompt/schema drift

- If `JobApplicationEvent` (Pydantic schema used for LLM structured output)
  changed, did the corresponding SQLModel table and `upsert_db` logic change
  in lockstep? Check both sides, it's easy to update one and silently drop a
  field on the other.
- If a node's prompt changed, does it still request exactly the fields the
  downstream schema requires, and does the errors/low-confidence routing
  branch still work?

## Config-driven platform list

- If a new platform was added, was it added to `backend/config/sources.yaml` rather
  than as new parsing/scraping code? New per-platform *code* (beyond config)
  is a sign the "LLM extraction over per-platform parsers" principle is being
  violated, flag it.
