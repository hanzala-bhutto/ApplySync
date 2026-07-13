# Disambiguation agent evidence gate

## Motivation
A merge verdict (same_application/duplicate) should never be accepted on a plausible-sounding guess alone.

## Problem
The agent's documented reliability gap is "shaky/hallucinated reasoning on real syncs": it could submit a `same_application` or `duplicate` verdict without ever having looked at a candidate's real status history or source email, reasoning purely from the company/title text already in its prompt. #61 stopped a bad tool arg from crashing the agent, but did nothing to stop a merge decided without evidence.

## Solution
Track, per run, which candidate ids an evidence tool (`get_status_history` or `read_source_email`) was actually called for. `submit_verdict` rejects a `same_application`/`duplicate` decision whose `matched_application_id` was never evidence-checked, returning an error the model must act on (gather evidence, then resubmit) rather than silently accepting the merge. `different_application` (creating a new row, not merging) needs no such check - it's always the safe/reversible choice. If the agent exhausts its turns without ever satisfying the gate, it fails open to a new application, same as any other agent error.

## Changes
- `backend/applysync/research/disambiguate.py`: `evidence_gathered` tracking in `run_disambiguation`, the gate in `submit_verdict`, and updated system prompt language telling the model the requirement up front (so it doesn't waste turns discovering it by trial and error)
- `tests/test_disambiguate.py`: new tests for the gate (rejected without evidence, accepted after gathering it, different_application needs none), existing merge-verdict tests updated to gather evidence first

## Benefits
- Structurally closes the "guessed from text alone" failure mode: a hallucinated or unverified merge can no longer complete, it can only ever fail open to an extra row.
- No new infrastructure - reuses the two evidence tools that already existed, just makes their use before merging non-optional.
