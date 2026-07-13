# Status-ordering and job-title extraction fixes

## Motivation
An application's tracked status and title should always reflect reality, regardless of what order its emails happen to be processed in.

## Problem
Two real bugs found by running this project's first-ever full historical resync (hundreds of emails processed in one batch, unlike the small incremental syncs that had masked both issues until now):

1. **Status set by processing order, not event date.** `add_status_event` unconditionally overwrote `current_status` with whatever event was just added. Gmail's search API returns results newest-first, not chronologically, so a batch sync processes an application's emails in an arbitrary order - whichever email happened to be processed *last* won, even if it was chronologically the *oldest*. Confirmed for real: an application ended up stuck on `applied` despite chronologically later `rejected` and `interview` events already on record.
2. **Process-step descriptions extracted as job_title.** The model sometimes extracts the *type* of interview/process step ("Technical Interview", "AI-powered video interview", "Online Assessment") as the job_title instead of the actual role - a placeholder-normalization gap the same class as the existing `_PLACEHOLDER_JOB_TITLES` set didn't cover.

## Solution
1. `add_status_event` now only advances `current_status` if the new event's `event_date` is the latest on record for that application, comparing against the true max `event_date` in the DB rather than trusting insertion order. Manual corrections (`set_manual_status`) always use `event_date=now()`, so they're unaffected and always win.
2. A regex-based defensive net (`_PROCESS_STEP_JOB_TITLE_RE`) normalizes job titles that are entirely qualifier + process words (e.g. "Technical Interview", "AI-powered video interview") to the `(unspecified role)` sentinel, alongside sharpened STEP 3 prompt guidance telling the model not to extract a process-step description or an interviewer's own title as the job_title in the first place.

## Changes
- `backend/applysync/db/repository.py`: `add_status_event`'s latest-event check, `_as_aware_utc` helper for comparing the pre-existing mix of timezone-aware/naive `event_date` values safely
- `backend/applysync/pipeline/nodes.py`: `_PROCESS_STEP_JOB_TITLE_RE`, `_normalize_job_title` extended, STEP 3 prompt guidance
- `tests/test_repository.py`, `tests/test_pipeline_nodes.py`: regression tests for both

## Benefits
- `current_status` is now correct regardless of the order a batch sync happens to process emails in - the class of bug that would otherwise silently misreport status for every multi-email application caught in an out-of-order batch.
- Fewer junk applications titled after an interview type or process step instead of a real role.
