"""One-time/idempotent seed of the four pipeline/research prompts into the
running Langfuse instance's Prompt Management, so `applysync.prompts.get_prompt`
has something to fetch on first run instead of always falling back to the
local constant (see docs/feasibility/langfuse-prompt-management.md).

Each prompt is created (or a new version added, if the text changed) under the
`production` label from the current local Python constant - the constants stay
the source of truth for what "production" should contain; this script is how
that text gets INTO Langfuse, not the other way around. Safe to re-run: if the
text already matches the latest version under `production`, Langfuse dedupes
and no new version is created.

Usage (from repo root, venv active, LANGFUSE_PUBLIC_KEY/SECRET_KEY set):
    python backend/scripts/seed_langfuse_prompts.py
"""
from __future__ import annotations

import sys

from applysync.config import get_settings
from applysync.pipeline.nodes import _CLASSIFY_AND_EXTRACT_PROMPT, _RELEVANCE_ONLY_PROMPT
from applysync.research.company import _RESEARCH_PROMPT
from applysync.research.disambiguate import _SYSTEM_PROMPT

_PROMPTS = {
    "classify_and_extract": _CLASSIFY_AND_EXTRACT_PROMPT,
    "relevance_only": _RELEVANCE_ONLY_PROMPT,
    "company_research": _RESEARCH_PROMPT,
    "disambiguation_system": _SYSTEM_PROMPT,
}


def main() -> int:
    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        print("LANGFUSE_PUBLIC_KEY/SECRET_KEY not set, nothing to seed.", file=sys.stderr)
        return 1

    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )

    for name, text in _PROMPTS.items():
        prompt = client.create_prompt(name=name, prompt=text, labels=["production"], type="text")
        print(f"{name}: version {prompt.version} (label production)")

    client.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
