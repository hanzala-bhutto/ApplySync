from __future__ import annotations

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from applysync.config import Settings

# NVIDIA's free tier caps at 40 requests/minute; throttling client-side keeps
# us under that instead of hitting 503 "worker limit reached" and burning
# retry/backoff time. max_bucket_size=1 means no burst above the steady rate.
_NVIDIA_FREE_TIER_RPM = 40


def get_chat_model(settings: Settings) -> ChatNVIDIA:
    rate_limiter = InMemoryRateLimiter(
        requests_per_second=_NVIDIA_FREE_TIER_RPM / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=1,
    )
    return ChatNVIDIA(
        model=settings.llm_model,
        api_key=settings.nvidia_api_key,
        rate_limiter=rate_limiter,
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
