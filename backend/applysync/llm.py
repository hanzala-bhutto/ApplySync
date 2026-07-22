from __future__ import annotations

from functools import lru_cache

from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_groq import ChatGroq
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from applysync.config import Settings


@lru_cache
def _limiter(rpm: int) -> InMemoryRateLimiter:
    """One shared limiter per requests-per-minute value. Rate caps are per
    account, so every model on the same account (all NVIDIA calls, or all Groq
    calls) must draw on the same limiter - handing each model its own would let
    them jointly exceed the real cap. Cached by rpm, so NVIDIA (40) and Groq
    (30) each get one shared instance."""
    return InMemoryRateLimiter(
        requests_per_second=rpm / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=1,
    )


def get_chat_model(settings: Settings, *, model_name: str | None = None) -> ChatNVIDIA:
    """NVIDIA model for extraction, scrutiny, and escalation. model_name
    overrides settings.llm_model (used to build the larger escalation model)."""
    return ChatNVIDIA(
        model=model_name or settings.llm_model,
        api_key=settings.nvidia_api_key,
        rate_limiter=_limiter(40),  # NVIDIA free tier
        temperature=0,  # extraction must be repeatable, not creative
        model_kwargs={"chat_template_kwargs": {"thinking": False}},  # skip Nemotron's reasoning step
    )


def get_agent_model(settings: Settings) -> ChatGroq | None:
    """Groq model for the disambiguation agent (fast, separate rate budget).
    Returns None when Groq isn't configured, so the agent falls back to the
    NVIDIA escalation model as before. The Groq-to-NVIDIA runtime fallback is
    composed with .with_fallbacks() at the call site."""
    if not (settings.groq_api_key and settings.groq_agent_model):
        return None
    return ChatGroq(
        model=settings.groq_agent_model,
        api_key=settings.groq_api_key,
        rate_limiter=_limiter(30),  # Groq free tier, its own account
        temperature=0,
    )
