from __future__ import annotations

from datetime import date

from applysync.config import SourcesConfig


def build_search_query(sources: SourcesConfig, after: date | None = None) -> str:
    """Build a Gmail search query scoped to application-confirmation phrasing.

    Deliberately NOT scoped to sender domain: application confirmations come
    from an unenumerable set of senders (every ATS vendor, every company's own
    domain), so a domain allowlist misses most of them. Confirmed against a
    real inbox: SmartRecruiters, Personio, Ashby, join.com, Teamtailor,
    Rippling, Workday, onlyfy.jobs, and direct company domains all showed up
    searching by subject phrase alone with zero domain filtering. Add a new
    phrase to confirmation_keywords in sources.yaml, not a new domain here.
    """
    keywords = sorted(set(sources.confirmation_keywords))
    keyword_clause = " OR ".join(_quote_if_needed(kw) for kw in keywords)

    query = f"({keyword_clause})"
    if after is not None:
        query += f" after:{after.strftime('%Y/%m/%d')}"
    return query


def guess_platform(sender: str, sources: SourcesConfig) -> str | None:
    """Best-effort platform id from a sender address, for dashboard labeling
    only. Returns None for senders not in the known list (e.g. a company's
    own domain, or an ATS vendor not yet added to sources.yaml) - that is
    expected and fine, not an error; the message is still processed.
    """
    sender_lower = sender.lower()
    for platform in sources.platforms:
        if any(domain in sender_lower for domain in platform.sender_domains):
            return platform.id
    return None


def _quote_if_needed(keyword: str) -> str:
    return f'subject:"{keyword}"' if " " in keyword else f"subject:{keyword}"
