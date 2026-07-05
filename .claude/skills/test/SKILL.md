---
name: test
description: Run or write tests for the ApplySync project following its specific conventions (mocking LLM calls, throwaway SQLite fixtures, idempotency double-run checks, eval runner). Use when the user asks to test, verify, or add test coverage for this project.
---

# Project test conventions

This project mixes deterministic code (DB, query building) with LLM-backed
nodes (classify/extract/match). Test each according to what it actually is:
don't mock things that are pure, and don't make real LLM calls in unit tests.

## Conventions

- **LangGraph nodes that call an LLM** (`classify_relevant`,
  `extract_structured_data`, the LLM-fallback path of
  `match_existing_application`): mock the LLM call at the LangChain model
  interface boundary (e.g. stub `.invoke`/`.with_structured_output(...).invoke`
  return values) rather than mocking `requests`/HTTP, keeps tests fast and
  independent of provider SDK internals. Assert on the *routing decision* the
  node makes (e.g. does a low-confidence extraction correctly route to the
  errors path), not on prompt wording.
- **Deterministic nodes** (`upsert_db`, `query_builder.py`,
  `db/repository.py`): test directly, no mocking, these should be pure enough
  to hit a real (throwaway) SQLite DB.
- **DB tests**: use a fresh in-memory or temp-file SQLite DB per test (via
  `db/init_db.py`'s schema creation), never the user's real tracker DB. Tear
  down after each test.
- **Idempotency check (M2 acceptance test)**: run the full pipeline twice over
  the same fixed batch of fake/fixture emails; assert the second run inserts
  zero new rows into `applications`/`status_events` and that `processed_emails`
  contains every message id exactly once.
- **Duplicate-linking check**: feed a synthetic "status update" email for an
  already-existing application and assert `match_existing_application` returns
  `update_existing`, not `new_application`.
- **Gmail client**: never test against the real Gmail API in automated tests.
  Use recorded/fixture email payloads (raw subject/sender/body dicts) so tests
  don't need network access or credentials.
- **Eval set (phase 2, once `eval/` exists)**: run via `eval/run_eval.py`
  against the hand-labeled samples in `eval/samples/{platform}/*.json`; report
  per-field accuracy, not just pass/fail, since partial extraction (e.g. right
  company, wrong date) is meaningfully different from total failure.

## Running tests

Use whatever test runner is declared in `pyproject.toml` (pytest is the
expected default for this stack). Look there before assuming a command.

## When adding new tests

Match the pattern of the code being tested: LLM-backed node, mock at the
model boundary and assert routing; deterministic code, real throwaway DB, no
mocks. Don't introduce a new testing pattern without checking `tests/` for
what's already established.
