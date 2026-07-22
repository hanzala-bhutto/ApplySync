# Cross-provider disambiguation agent

## Motivation
The disambiguation agent's multi-turn tool loop is the slowest, most latency-sensitive LLM work in a sync, and on NVIDIA it competes with bulk extraction for the same 40 RPM budget.

## Problem
Sharing one NVIDIA rate budget makes agent calls spike to 15-30s under load, and a real-data A/B surfaced a wrong verdict on a clear same-application pair (shared SAP requisition ID) that no prompt wording fixed.

## Solution
Run the agent on Groq (a separate account, its own rate budget, ~8x lower latency) with automatic fallback to the NVIDIA escalation model via `.with_fallbacks()`, and resolve shared-requisition-ID cases deterministically in Python before any model runs.

## Changes
- `llm.py`: `get_agent_model` (`ChatGroq`), one shared `_limiter(rpm)` per account
- `research/disambiguate.py`: `fallback_model` composed with `.with_fallbacks()`, requisition-ID short-circuit
- `pipeline/nodes.py`, `pipeline/graph.py`: thread `agent_model` to the disambiguation node
- `config.py` (`groq_api_key`/`groq_agent_model`), `pyproject.toml` (`langchain-groq`)

## Benefits
- Agent verdicts in ~1-3s instead of 15-30s, and extraction keeps NVIDIA's full 40 RPM.
- Faster in the common case, no worse in the worst case: a Groq outage or rate-limit transparently falls back to NVIDIA.
- The requisition-ID short-circuit fixes a real accuracy miss for free, provider-agnostically.
