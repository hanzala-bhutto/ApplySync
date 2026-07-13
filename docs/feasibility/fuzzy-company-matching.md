# Fuzzy / alias company matching

## Motivation
The match step should recognize the same company across a typo or a name variant, not just an exact string.

## Problem
Exact-string company comparison misses real fragmentation: a typo ("EGYM" vs "EGYG") or a word-add variant ("Galvany" vs "Galvany Energy") creates a second application row instead of matching the first. It also blocks the disambiguation agent from ever firing on these cases, since today's candidate generation (`find_candidate_applications`) is itself exact-company.

## Solution
Compare normalized company names with a single `rapidfuzz` fuzzy-ratio score (e.g. `token_sort_ratio`) above a tuned threshold, covering both the typo and word-add cases with one function. The title must still match exactly regardless of the company score, so two different roles at the same company stay distinct. Any non-exact-string (fuzzy) company hit always routes to the disambiguation agent for confirmation, even when the title also matches exactly, keeping over-merge risk near zero. A one-off cleanup pass finds and offers to merge existing typo/alias dupes already in the database.

## Changes
- New `rapidfuzz` dependency
- `repository.py`: `find_matching_application` and `find_candidate_applications`, relax company comparison to fuzzy while keeping title exact
- Conditional edge in `pipeline/graph.py` so any fuzzy (non-exact) company match routes to `disambiguate_match`
- `backend/scripts/merge_duplicate_applications.py`: extend to detect fuzzy-company dupes for the cleanup pass

## Benefits
- Closes the exact-string blind spot that let real typo/word-add dupes (EGYM/EGYG, Galvany/Galvany Energy) slip through as separate rows.
- Two different roles at the same company still correctly stay distinct, since the title match remains exact.
- No new over-merge risk: every fuzzy hit is agent-confirmed, never auto-merged.
