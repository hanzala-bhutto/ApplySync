# Langfuse observability

## Motivation
This project's own history (misclassification, wrong disambiguation merges, the pagination/lookback bugs) has repeatedly shown that pipeline problems only surface against real syncs, and today the only way to inspect one is re-running the eval harness or reading plain logs.

## Problem
There is no per-node, per-call visibility into a real sync: no way to see a single email's path through scrutinize_relevance -> classify_and_extract -> match_existing_application -> disambiguate_match -> upsert_db, what the LLM actually saw and returned at each step, which calls used the escalation model, or what the disambiguation/research agents' tool loops did, without adding print statements each time.

## Solution
Self-hosted Langfuse (not LangSmith, which would ship email bodies to a third party and contradict the local-first keyless design) running in docker-compose next to SearXNG. Wrap the compiled LangGraph run with Langfuse's LangChain callback handler so every node, LLM call, and tool call in a sync is traced automatically, tagged with which model (fast vs escalation) served each call.

## Changes
- `langfuse/` (docker-compose.yml, self-hosted server + Postgres)
- `backend/applysync/observability.py` (callback handler construction, DI-style like `get_search_client`/`get_llm_model`)
- `backend/applysync/pipeline/graph.py` (`run_sync`/`process_emails` pass the callback handler into `graph.stream`, and tag each email's trace with the sync's `run_id` as a Langfuse session so a whole sync is one browsable unit, not just per-email traces)
- `backend/applysync/research/disambiguate.py`, `research/company.py` (same callback threaded into the agents' model calls)
- `.env`/`config.py`: Langfuse host + keys, optional (tracing off if unset, same fail-open posture as the rest of the pipeline)
- `backend/scripts/pull_flagged_traces.py`: closes the loop from "I noticed this is wrong in the Langfuse dashboard" to the eval harness actually tracking it. Convention: score a trace `correct=false` (Boolean score config) in the Langfuse UI, optionally with a comment on what the right label should be; the script pulls every such trace, prefills an unverified `EvalSample` from the trace's own input email + (wrong) output, and merges it into `eval/samples/gold.json` for a human correction pass - same verify-before-trust discipline `build_eval_dataset.py` already established.

## Benefits
- Real per-node latency and input/output visibility on every sync, without touching production logging code each time something needs debugging.
- Tool calls and reasoning inside the disambiguation agent become inspectable after the fact, directly addressing the "shaky/hallucinated reasoning" and wrong-merge problems already on record.
- Self-hosted, so no email content ever leaves the machine.
- A whole sync groups into one Langfuse session instead of scattered per-email traces, and mistakes spotted while browsing feed directly back into the eval gold dataset instead of being noticed once and forgotten.
