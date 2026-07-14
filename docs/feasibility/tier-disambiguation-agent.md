# Tier the disambiguation agent onto the escalation model

## Motivation
M5 step 4 ("Tiered models") in CLAUDE.md specifically names the disambiguation agent as the target for the larger model, since it's already low-volume (~50 calls per full sync vs. 500 for extraction). The prior tiering pass (relevance-classification-accuracy) added `settings.llm_escalation_model` and wired it into `scrutinize_relevance` and `classify_and_extract`, but left `make_disambiguate_node` on the fast nano model - the item M5 step 4 actually called out was still undone.

## Problem
The disambiguation agent decides whether an ambiguous email is a genuinely separate application, a status update, or a duplicate - a wrong "same_application"/"duplicate" verdict silently merges two distinct applications, which is not recoverable the way a redundant new row is. It's exactly the case where the fast model's speed advantage matters least (low call volume) and its judgment quality matters most (irreversible mistake).

## Solution
Add an `escalation_model` parameter to `make_disambiguate_node`, defaulting to `None` (falls back to `model`, matching the existing tiering pattern). Unlike the scrutiny/extract tiering, this isn't gated on a failure signal - the node is already infrequent, so it always prefers the escalation model when one is configured. `run_sync` already constructs `escalation_model` and threads it through `build_graph`, so this is a one-line change at the `make_disambiguate_node` call site plus the new parameter.

## Changes
- `backend/applysync/pipeline/nodes.py`: `escalation_model` param on `make_disambiguate_node`, used unconditionally when present
- `backend/applysync/pipeline/graph.py`: passes `escalation_model` through to `make_disambiguate_node`

## Benefits
- Closes the specific gap named in CLAUDE.md's M5 roadmap for tiered models.
- No behavior change for callers that don't pass `escalation_model` (unit tests, degraded runs) - existing tests needed no changes.
- No automated eval covers merge/disambiguation accuracy yet (documented gap in CLAUDE.md - needs seeded DB state per sample), so this change is judged by the model swap being low-risk/backward-compatible rather than a measured before/after number; a follow-up eval for this stage is still open work.
