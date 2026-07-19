# Disambiguation agent: precomputed date-arithmetic annotations

## Motivation
The disambiguation agent's judged accuracy needs to be trustworthy before it can be used to decide whether merges are safe to keep, tighten, or loosen.

## Problem
Running the LLM-as-judge evaluators (`docs/feasibility/langfuse-llm-judge.md`) against a real full-history sync session (`cbfa5ab2`, 500 emails) flagged disambiguation at 79.5% (97/122) - well below relevance (99.5%) and extraction (96.4%). Investigating the flagged cases surfaced a judge blind spot first: the `disambiguate_match` observation's own input/output doesn't include what the agent's `get_status_history`/`read_source_email` tool calls actually returned (those are sibling trace observations), so the judge was scoring "hallucination" on reasoning it had no way to verify. Fixing the judge (feeding it the real tool-call evidence) raised the honest baseline to 91.0% (111/122) - most of the gap was a measurement artifact, not an agent bug.

Of the 11 genuinely flagged cases at the corrected baseline, 10 shared one exact root cause: the agent doing mental arithmetic on two raw RFC-2822 date strings and getting the chronological order backwards (e.g. reading "13 Jul 2026" vs "13 Jun 2026" as interchangeable, or concluding a rejection came before the application it referred to). The 11th flag was a judge self-contradiction, not an agent error.

## Solution
Stop asking the model to do date subtraction in its head. `research/disambiguate.py`'s `get_status_history` and `read_source_email` tools now compute the exact day delta between each historical date and the new email's date in Python (`_relative_to_new_email`, reusing the existing `_as_aware_utc` normalization pattern from `repository.py` for the naive/aware datetime mix already documented there) and annotate it inline, e.g. `"...Date: Mon, 13 Jul 2026 (5 days AFTER the new email)"`. The system prompt tells the agent to trust and quote that annotation rather than recompute it.

## Changes
- `backend/applysync/research/disambiguate.py`: `_parse_email_date`, `_as_aware_utc`, `_relative_to_new_email` helpers; both evidence tools now annotate returned dates; system prompt updated to point at the annotation
- `backend/scripts/run_llm_judge_backfill.py` (new): runs the three LLM-as-judge evaluators directly via the Langfuse API, since the self-hosted build in `langfuse/docker-compose.yml` doesn't expose the Traces-table "Actions -> Evaluate" backfill flow the hosted docs describe. Disambiguation judging includes the agent's real tool-call evidence (`_fetch_tool_evidence`), fixing the blind spot above.
- `backend/scripts/replay_disambiguation.py` (new): replays specific historical `disambiguate_match` observations through the current code (not just re-judges stale output), used to verify the fix changes real agent behavior.

## Benefits
- Verified live against the 10 genuinely-flagged cases (`replay_disambiguation.py` against real Gmail/DB data, not mocks): 5 flipped to the judge-endorsed correct verdict, 3 kept their (likely already-correct) verdict but replaced fabricated reasoning ("likely a typo in the year") with real computed evidence, 2 remain open but trace to a different, smaller root cause (which-of-several-candidates ambiguity, not date math) - not addressed here.
- `run_llm_judge_backfill.py` and `replay_disambiguation.py` are reusable for any future accuracy audit, not one-off scripts.
