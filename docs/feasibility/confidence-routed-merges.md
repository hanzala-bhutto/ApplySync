# Confidence-routed merges

## Motivation
An unrecoverable merge (attaching a new email to the wrong existing application, or dropping it as a duplicate) should never happen on a low-confidence guess without a human in the loop.

## Problem
The disambiguation agent applies its verdict silently: a `same_application` or `duplicate` verdict merges/skips into an existing row immediately. A wrong merge misattributes or silently loses a real application, and the agent carries only a free-text `reasoning`, no confidence signal to gate on. This is M5 step 3, the last unstarted reliability item.

## Solution
The agent's `submit_verdict` gains a scalar `confidence` enum (`high`/`medium`/`low`, not a float, since this model does unreliable numeric reasoning). A merge/duplicate verdict below a configurable bar (`disambiguation_min_auto_merge_confidence`, default `medium`, so only `low` routes) is downgraded to `new_application` for the immediate write (email always tracked, the recoverable direction the agent's error path already takes) and a `ReviewSuggestion` is queued advising the merge. Approve collapses the new row into the existing one (reusing the duplicate-cleanup merge logic); reject and reject-all stay pure no-ops (the two rows simply remain separate, nothing lost). `different_application`/`new_application` verdicts are never gated.

## Changes
- `research/disambiguate.py` + `pipeline/state.py`: `confidence` on `submit_verdict` and `DisambiguationVerdict`; prompt asks for it
- `pipeline/nodes.py`: `disambiguate_match` routes a below-bar merge/duplicate to `new_application` + review; `upsert_db` creates the `merge_into` `ReviewSuggestion`
- `db/repository.py`: `merge_applications(source_ids, target_id)` (extracted from the cleanup script's `merge_group`, reused by both); `create_review_suggestion` gains `confidence`; `approve_review_suggestion` handles `merge_into`
- `db/models.py` + `db/init_db.py`: `confidence` column on `ReviewSuggestion` + additive migration
- `config.py`: `disambiguation_min_auto_merge_confidence`
- `frontend`: `merge_into` label + confidence badge on the review card

## Benefits
- No low-confidence merge is ever auto-applied; the unrecoverable action always gets human sign-off.
- No email is ever lost: the recoverable action (a new row) happens immediately, exactly as the agent's existing fail-open path does.
- Reject/reject-all keep uniform no-op semantics (no surprise row creation), and the merge logic reuses the already-tested cleanup path.
