# Rename full_scan to full_audit

## Motivation
The pipeline-flow-visualization work (see docs/feasibility/pipeline-flow-visualization.md) surfaced that `full_scan.py` was never documented in CLAUDE.md's milestone list at all - it shipped 2026-07-06, a week before the `/feasibility` report workflow existed, so it predates the process that would have required writing this down. Documenting it surfaced a naming problem worth fixing before writing the docs, not after: "full-scan" reads as a bigger version of "sync," but it behaves nothing like sync.

## Problem
`run_sync` writes directly to `Application`/`StatusEvent` - that is what "sync" means everywhere else in this codebase. The old `full_scan` never does that: every disagreement between a fresh re-extraction and what's already stored becomes a `ReviewSuggestion` a human must approve, specifically because re-running the LLM pipeline over the *entire* historical inbox in one pass carries a real error rate that should never land in real data unreviewed (this is also why full_scan reuses only the two side-effect-free node factories - `scrutinize_relevance` and `classify_and_extract` - and never touches `match_existing_application`, `disambiguate_match`, or `upsert_db`; see pipeline-flow-visualization.md for the full node-by-node breakdown). Calling it "full-scan" (and a rejected rename candidate, "full-sync") both invite the same wrong assumption: that it writes, just at a larger scope.

## Solution
Renamed throughout to `full_audit`, a name that doesn't borrow "sync"'s implied write-through behavior:
- `pipeline/full_scan.py` -> `pipeline/full_audit.py`, `full_scan()` -> `full_audit()`, `process_full_scan()` -> `process_full_audit()`
- API endpoint `POST /api/sync/full-scan` -> `POST /api/sync/full-audit`
- `PipelineRun.run_type` value `"full_scan"` -> `"full_audit"` for new runs
- Frontend: `postFullScan` -> `postFullAudit`, "Full Scan" -> "Full Audit" throughout `Sync.tsx`/`Review.tsx`

No DB migration for existing rows: this project has no migration tooling (see CLAUDE.md), and `run_type` is a plain unvalidated string column, so older `PipelineRun` rows already written with `run_type="full_scan"` are left as-is. Display code (`runTypeLabel`, `FinishedSummary` in `Sync.tsx`, and the `PipelineRun.run_type`/`SyncStatus.current_run_type` TypeScript unions in `api.ts`) explicitly treats `"full_scan"` and `"full_audit"` as equivalent, so historical runs still display correctly under the new label rather than falling through to a generic/wrong one.

## Changes
- `backend/applysync/pipeline/full_audit.py` (renamed from `full_scan.py`)
- `backend/applysync/web/sync.py`: endpoint path, DI function names, `run_type` string
- `backend/applysync/db/models.py`, `db/repository.py`, `gmail/client.py`, `web/review.py`: comment references only
- `frontend/src/lib/api.ts`, `pages/Sync.tsx`, `pages/Review.tsx`: function/label/type renames, back-compat handling for the old stored value
- `frontend/e2e/sync-page.spec.ts`: one test deliberately keeps `run_type: 'full_scan'` as a fixture to exercise the back-compat display path, not just the new value
- `tests/test_full_scan.py` -> `tests/test_full_audit.py`, `tests/test_repository.py`, `tests/test_review.py`, `tests/test_sync.py`: identifier/string renames

## Benefits
- Removes a naming collision that would otherwise mislead anyone (including future sessions) into assuming this feature writes data the way `run_sync` does.
- Closes the CLAUDE.md documentation gap for this feature entirely - see the new milestone entry added alongside this report.
- Zero data risk: no schema change, no migration, existing historical runs remain fully readable and correctly labeled.
- All 228 backend tests and the frontend production build pass unchanged in behavior, confirming this was a pure rename with no logic change.
