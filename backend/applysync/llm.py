from __future__ import annotations

from functools import lru_cache

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from applysync.config import Settings

# NVIDIA's free tier caps at 40 requests/minute; throttling client-side keeps
# us under that instead of hitting 503 "worker limit reached" and burning
# retry/backoff time. max_bucket_size=1 means no burst above the steady rate.
_NVIDIA_FREE_TIER_RPM = 40


@lru_cache
def _shared_rate_limiter() -> InMemoryRateLimiter:
    """One limiter instance for the whole process, not one per get_chat_model
    call. The 40 RPM cap is per NVIDIA account, not per model - a run_sync
    that builds both the fast and escalation model used to hand each its own
    fresh InMemoryRateLimiter, so each thought it had the full 40 RPM to
    itself while the two together could exceed the account's real cap,
    producing 503s and the resulting retry/backoff latency spikes (seen for
    real in Langfuse traces: escalation-model calls occasionally taking
    15-30s+ instead of the usual 1-4s). A single shared limiter makes the
    fast and escalation models actually share one real budget."""
    return InMemoryRateLimiter(
        requests_per_second=_NVIDIA_FREE_TIER_RPM / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=1,
    )


def get_chat_model(settings: Settings, *, model_name: str | None = None) -> ChatNVIDIA:
    """model_name overrides settings.llm_model - used to construct the
    escalation model (see settings.llm_escalation_model) with the same
    rate limiter/temperature/reasoning config as the default model, just a
    different (larger, slower) underlying model name."""
    return ChatNVIDIA(
        model=model_name or settings.llm_model,
        api_key=settings.nvidia_api_key,
        rate_limiter=_shared_rate_limiter(),
        # Extraction should be conservative and repeatable, not creative:
        # re-running the same email through the same model at default
        # temperature produced a different status about a third of the time
        # in real testing (e.g. "rejected" one run, "offer" the next, for an
        # email that was neither).
        temperature=0,
        # Disables Nemotron's internal chain-of-thought step. Measured
        # against the real API: 2.17s with reasoning on vs 0.81s off. Kept
        # off for speed, but this alone did not explain the accuracy
        # problems found in testing - see the prompt in nodes.py for the
        # actual fix (explicit conservative-default-status instructions).
        model_kwargs={"chat_template_kwargs": {"thinking": False}},
    )
