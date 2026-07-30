# Langfuse prompt management

## Motivation
Langfuse is already self-hosted for tracing (`observability.py::get_langfuse_handler`); its separate Prompt Management feature (versioned prompts with labels, fetched at runtime) is unused.

## Problem
CLAUDE.md's own LLM section documents that extraction accuracy is fragile to prompt wording and has already caused real regressions (hallucinated statuses, false positives), yet every one of the four prompts (`nodes._CLASSIFY_AND_EXTRACT_PROMPT`, `nodes._RELEVANCE_ONLY_PROMPT`, `research/company.py::_RESEARCH_PROMPT`, `research/disambiguate.py::_SYSTEM_PROMPT`) is a hardcoded Python string constant, so a wording tweak needs a code change, PR, and redeploy, with no versioning or rollback.

## Solution
Fetch each prompt from Langfuse by name + label (`production`) at the call site instead of importing the local constant, with the local constant kept as the fallback (both the Langfuse SDK's own fetch-fails path and a wrapper try/except around client construction) so a missing/unreachable Langfuse instance never blocks a sync - the same "diagnostic, never load-bearing" posture already established for tracing.

## Changes
- `backend/applysync/prompts.py` (new): `get_prompt(name, local_fallback, settings, label)` - returns local_fallback whenever Langfuse keys are unset or the fetch fails; otherwise returns the fetched prompt text via the Langfuse SDK's own `get_prompt(..., fallback=local_fallback)` (which has its own default 60s cache).
- `pipeline/nodes.py`, `research/company.py`, `research/disambiguate.py`: replace the raw string constant used at `.format()` call sites with `get_prompt("<name>", _CONSTANT, settings=get_settings())`. The constants themselves stay in the code as the fallback/seed text - not deleted.
- `scripts/seed_langfuse_prompts.py` (new): one-time/idempotent script that creates or updates the four prompts in the running Langfuse instance with a `production` label, from the current local constants - so a fresh Langfuse instance has something to serve on first fetch.
- `CLAUDE.md`: move this from "Not built yet" to the shipped list.

## Benefits
- Prompt iteration no longer requires a code deploy - edit in the Langfuse UI, publish a new version under the `production` label.
- Versioning + labels give safe rollback of a bad prompt change without a git revert.
- No new infra - reuses the Langfuse instance already self-hosted for tracing.
