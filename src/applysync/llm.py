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
    return ChatNVIDIA(model=settings.llm_model, api_key=settings.nvidia_api_key, rate_limiter=rate_limiter)
