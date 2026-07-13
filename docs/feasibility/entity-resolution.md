# Entity / duplicate resolution agent

## Motivation
The match step should recognize when a new email belongs to an application already on record, even when the job title is missing or worded differently.

## Problem
The pure heuristic (company + title + platform) could not tell a missing title from a genuinely different one for the same company, so ambiguous cases silently created a new application row - exactly how duplicates leaked in (the real Nagarro/EGYM cases).

## Solution
Route only the ambiguous case (same company+platform, no exact title match) to a hand-rolled LLM tool-loop agent that reads a candidate's history, diffs its source email against the new one, can do a SearXNG entity check, and submits a verdict mapped onto the existing `MatchDecision`.

## Changes
- `backend/applysync/research/disambiguate.py` (the agent)
- `repo.find_candidate_applications`; conditional edge off `match_existing_application` in `pipeline/graph.py`
- `tests/test_disambiguate.py`

## Benefits
- Fewer spurious duplicate rows; the former missing-title gap is now a reasoned decision, not a blind guess.
- Fails open to a new application on any error and only runs for genuinely ambiguous cases, so it never blocks or slows the clear ones.
