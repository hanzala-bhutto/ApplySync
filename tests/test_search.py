import httpx
import pytest

from applysync.search import SearchResult, SearxngClient, SearxngError


def _client(handler) -> SearxngClient:
    """Build a SearxngClient wired to a mocked transport, so tests never need a
    running SearXNG instance."""
    transport = httpx.MockTransport(handler)
    return SearxngClient("http://searxng.test", client=httpx.Client(transport=transport))


def test_search_parses_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "egym se company"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "EGYM - Wikipedia",
                        "url": "https://en.wikipedia.org/wiki/EGYM",
                        "content": "EGYM GmbH is a Munich-based fitness technology company.",
                        "engine": "duckduckgo",
                    },
                    {
                        "title": "EGYM careers",
                        "url": "https://egym.com/careers",
                        "content": "",
                        "engine": "google",
                    },
                ]
            },
        )

    results = _client(handler).search("egym se company")

    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].url == "https://en.wikipedia.org/wiki/EGYM"
    assert "Munich" in results[0].content


def test_search_respects_max_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": f"https://example.com/{i}", "title": str(i)} for i in range(10)]},
        )

    results = _client(handler).search("anything", max_results=3)

    assert len(results) == 3


def test_search_passes_time_range_and_categories():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["time_range"] = request.url.params.get("time_range")
        captured["categories"] = request.url.params.get("categories")
        return httpx.Response(200, json={"results": []})

    _client(handler).search("acme layoffs", time_range="month", categories="news")

    assert captured["time_range"] == "month"
    assert captured["categories"] == "news"


def test_search_skips_results_without_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"title": "no url here"}, {"url": "https://ok.com", "title": "ok"}]},
        )

    results = _client(handler).search("anything")

    assert len(results) == 1
    assert results[0].url == "https://ok.com"


def test_search_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(SearxngError):
        _client(handler).search("anything")


def test_search_raises_on_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(SearxngError):
        _client(handler).search("anything")


def test_search_raises_when_no_results_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": []})

    with pytest.raises(SearxngError):
        _client(handler).search("anything")
