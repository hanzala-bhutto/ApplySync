from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel

from applysync.config import Settings

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """One web result from SearXNG, trimmed to the fields the research features
    actually use. `content` is the result snippet, not the full page."""

    title: str
    url: str
    content: str = ""
    engine: str = ""


class SearxngError(RuntimeError):
    """Raised when a search cannot be completed (service down, bad status,
    malformed response). Callers that must not fail hard on this - e.g. an
    agent node enriching data best-effort - should catch it and degrade
    gracefully, the same way scrutinize_relevance fails open on an LLM error."""


class SearxngClient:
    """Thin client over a self-hosted SearXNG instance's JSON API.

    Deliberately does no caching or retrying itself: those are cross-cutting
    concerns the callers (agents, a cache layer) layer on, so this stays a
    single-responsibility transport. One shared instance is safe to reuse -
    httpx.Client is thread-safe for issuing requests.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # An injected client is used as-is (tests pass a MockTransport-backed
        # one); otherwise we own the lifecycle of the one we create.
        self._client = client or httpx.Client(timeout=timeout)

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        categories: str | None = None,
        time_range: str | None = None,
        language: str = "en",
    ) -> list[SearchResult]:
        """Run a search and return up to `max_results` parsed results.

        `time_range` (one of day/week/month/year) narrows to recent results,
        which is what the company-health / recent-news lookups want. Raises
        SearxngError on any failure rather than returning an empty list, so a
        real outage is never silently indistinguishable from "no results".
        """
        params: dict[str, str] = {"q": query, "format": "json", "language": language}
        if categories:
            params["categories"] = categories
        if time_range:
            params["time_range"] = time_range

        try:
            response = self._client.get(f"{self._base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise SearxngError(f"SearXNG request failed: {exc}") from exc
        except ValueError as exc:  # json() on a non-JSON body
            raise SearxngError(f"SearXNG returned a non-JSON response: {exc}") from exc

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise SearxngError("SearXNG response had no 'results' list")

        results: list[SearchResult] = []
        for item in raw_results[:max_results]:
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("title", "") or "",
                    url=url,
                    content=item.get("content", "") or "",
                    engine=item.get("engine", "") or "",
                )
            )
        return results


def get_search_client(settings: Settings) -> SearxngClient:
    """Factory matching the project's dependency-injection pattern
    (get_gmail_client / get_llm_model), so callers and tests can swap in a
    fake instead of hitting a real SearXNG instance."""
    return SearxngClient(settings.searxng_url)
