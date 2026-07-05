from __future__ import annotations

from datetime import date

from applysync.config import SourcesConfig


def build_search_query(sources: SourcesConfig, after: date | None = None) -> str:
    """Build a Gmail search query scoped to known platforms, so the pipeline
    never has to scan the whole inbox. Add a new platform to sources.yaml,
    not to this function.
    """
    domains = sorted({domain for platform in sources.platforms for domain in platform.sender_domains})
    keywords = sorted({kw for platform in sources.platforms for kw in platform.subject_keywords})

    domain_clause = " OR ".join(f"from:{domain}" for domain in domains)
    keyword_clause = " OR ".join(_quote_if_needed(kw) for kw in keywords)

    query = f"({domain_clause}) ({keyword_clause})"
    if after is not None:
        query += f" after:{after.strftime('%Y/%m/%d')}"
    return query


def guess_platform(sender: str, sources: SourcesConfig) -> str | None:
    """Best-effort platform id from a sender address, used as the
    heuristic-first step before falling back to an LLM classification.
    """
    sender_lower = sender.lower()
    for platform in sources.platforms:
        if any(domain in sender_lower for domain in platform.sender_domains):
            return platform.id
    return None


def _quote_if_needed(keyword: str) -> str:
    return f'subject:"{keyword}"' if " " in keyword else f"subject:{keyword}"
