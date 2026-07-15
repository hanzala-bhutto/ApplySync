# Langfuse LLM-as-judge + granular score configs

## Motivation
The existing "flag a bad trace" workflow depends entirely on a human noticing and manually scoring it - most mistakes are never seen.

## Problem
`pull_flagged_traces.py` only pulls traces a human scored `correct=false` in the Langfuse UI. That single Boolean also can't say *which* stage broke (scrutiny, extraction, or disambiguation each fail differently, per the eval harness's own per-stage philosophy) - a human has to open the trace and figure that out by hand every time.

## Solution
Two additive pieces, self-hosted, no new infrastructure:
1. **Granular score configs**: three Boolean configs (`relevance_correct`, `extraction_correct`, `disambiguation_correct`) replacing the single `correct` umbrella, mirroring the eval harness's per-stage metrics.
2. **LLM-as-judge evaluators** (fully open-source/MIT since Langfuse v3.65+, confirmed available on our running v3.213.0): one judge prompt per stage, run manually/on-demand (not live) against a chosen time range of traces via the Langfuse UI's Actions -> Evaluate backfill flow. Manual, not live, specifically to avoid the judge's own LLM calls contending with the pipeline's shared 40 RPM NVIDIA budget (see `fix/shared-nvidia-rate-limiter`, PR #79) during an actual sync - judging happens whenever the user chooses, fully decoupled from sync timing.

The judge reuses the existing NVIDIA connection (registered once in Langfuse's own Settings -> LLM Connections as a custom OpenAI-compatible endpoint, `https://integrate.api.nvidia.com/v1`) rather than a separate provider/account, since manual triggering already removes the rate-limit contention concern that would otherwise argue for isolation.

## Changes
- Langfuse UI (one-time, not code): 1 LLM Connection (NVIDIA), 3 score configs, 3 evaluators (judge prompt + manual trigger each)
- `backend/scripts/pull_flagged_traces.py`: read the three granular scores instead of the single `correct`
- This doc; no pipeline/agent code changes - purely observability-side

## Benefits
- Mistakes get caught even when no human happens to look, closing the gap in the existing flagged-traces loop.
- A judged trace immediately says which stage failed, not just "something's wrong" - directly useful triage signal.
- Zero rate-limit risk to real syncs, since judging is always a separate, user-initiated action.
