from __future__ import annotations

import logging
from functools import lru_cache

from applysync.config import Settings

logger = logging.getLogger(__name__)


@lru_cache
def _get_client(public_key: str, secret_key: str, host: str):
    from langfuse import Langfuse

    return Langfuse(public_key=public_key, secret_key=secret_key, host=host)


def get_prompt(name: str, local_fallback: str, *, settings: Settings, label: str = "production") -> str:
    """Fetch a prompt's raw text from Langfuse Prompt Management by name+label.

    Same "diagnostic, never load-bearing" posture as observability.get_langfuse_handler:
    a fresh checkout before `docker compose up -d` in langfuse/, an unreachable
    Langfuse instance, or a prompt that was never created there must never block a
    sync - this always returns local_fallback (the hardcoded constant the caller
    already had) in that case. The Langfuse SDK's own get_prompt() handles the
    fetch-fails-but-cache-has-a-stale-copy case and the fully-cold-cache case (via
    its `fallback` arg) with its own default 60s in-process cache; the try/except
    here only guards client construction and everything else that isn't fetch
    itself (e.g. Langfuse unreachable on the very first call, before anything is
    cached).

    Prompts keep the same `{name}`-style (single curly brace) placeholders as the
    local constants and are formatted with plain str.format() at the call site,
    same as before - not Langfuse's own double-curly-brace compile().
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return local_fallback

    try:
        client = _get_client(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_host)
        prompt = client.get_prompt(name, label=label, fallback=local_fallback)
        return prompt.prompt
    except Exception as exc:  # noqa: BLE001 - a prompt-fetch failure must never block a sync
        logger.warning("Langfuse prompt fetch failed for %r, using local fallback: %s", name, exc)
        return local_fallback
