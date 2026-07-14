from __future__ import annotations

import logging

from applysync.config import Settings

logger = logging.getLogger(__name__)


def get_langfuse_handler(settings: Settings):
    """Builds a Langfuse LangChain callback handler for tracing a sync's
    LangGraph run (every node, LLM call, and the disambiguation agent's tool
    loop - callbacks propagate automatically to nested `.invoke()` calls made
    within a node's own execution, no per-node wiring needed) or a standalone
    agent call like company research.

    Returns None whenever tracing isn't configured (settings.langfuse_public_key/
    secret_key unset - a fresh checkout before the user has run `docker compose
    up -d` in langfuse/, or unit tests) or the client fails to initialize
    (e.g. the Langfuse stack isn't running). The pipeline must behave
    identically either way - tracing is diagnostic, never load-bearing -
    matching the fail-open posture of scrutinize_relevance and the
    disambiguation agent.
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        return CallbackHandler()
    except Exception as exc:  # noqa: BLE001 - tracing must never block a sync
        logger.warning("Langfuse init failed, tracing disabled: %s", exc)
        return None
