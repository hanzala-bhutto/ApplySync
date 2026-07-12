from datetime import date

import pytest

from applysync.db import repository as repo
from applysync.research import CompanyProfileResult, ResearchError, research_company
from applysync.search import SearchResult, SearxngError
from applysync.web.app import get_llm_model, get_search_client
from tests.fakes import FakeCompletionModel, FakeSearchClient

# --- unit tests for research_company (research/company.py) ---


def _results():
    return [
        SearchResult(
            title="EGYM - fitness technology",
            url="https://egym.com",
            content="EGYM is a Munich-based fitness technology company.",
        ),
        SearchResult(
            title="EGYM raises Series F",
            url="https://news.example/egym-series-f",
            content="EGYM raised 200M in a Series F round.",
        ),
    ]


def _model_returning(profile: CompanyProfileResult) -> FakeCompletionModel:
    # research_company parses model output text with PydanticOutputParser, so
    # the fake returns the profile serialized as JSON (what the real model
    # emits under the parser's format instructions).
    return FakeCompletionModel(content=profile.model_dump_json())


def test_research_company_grounds_and_returns_sources():
    profile_result = CompanyProfileResult(
        summary="Fitness technology company.",
        industry="Fitness technology",
        headquarters="Munich, Germany",
        website="https://egym.com",
        recent_news="Raised a Series F round.",
    )
    profile, sources = research_company(
        "EGYM", search_client=FakeSearchClient(results=_results()), model=_model_returning(profile_result)
    )

    assert profile.headquarters == "Munich, Germany"
    assert profile.recent_news == "Raised a Series F round."
    # Source urls come from the search results, for human verification.
    assert sources == ["https://egym.com", "https://news.example/egym-series-f"]


def test_research_company_empty_when_no_results():
    profile, sources = research_company(
        "Nonexistent Co",
        search_client=FakeSearchClient(results=[]),
        model=_model_returning(CompanyProfileResult()),
    )

    assert profile.summary is None
    assert sources == []


def test_research_company_raises_on_search_failure():
    with pytest.raises(ResearchError):
        research_company(
            "EGYM",
            search_client=FakeSearchClient(exception=SearxngError("service down")),
            model=_model_returning(CompanyProfileResult()),
        )


def test_research_company_raises_on_llm_failure():
    with pytest.raises(ResearchError):
        research_company(
            "EGYM",
            search_client=FakeSearchClient(results=_results()),
            model=FakeCompletionModel(exception=RuntimeError("llm boom")),
        )


def test_research_company_raises_on_unparseable_output():
    with pytest.raises(ResearchError):
        research_company(
            "EGYM",
            search_client=FakeSearchClient(results=_results()),
            model=FakeCompletionModel(content="this is not the json you are looking for"),
        )


# --- repository cache tests ---


def test_company_profile_cache_roundtrip_and_key_normalization(session):
    repo.upsert_company_profile(
        session, display_name="EGYM", summary="Fitness tech.", industry="Fitness",
        company_size=None, headquarters="Munich", website="https://egym.com",
        recent_news="Raised a round.", source_urls=["https://egym.com"],
    )

    # Lookup is case/whitespace-insensitive via company_key.
    found = repo.get_company_profile(session, "  egym ")
    assert found is not None
    assert found.summary == "Fitness tech."
    assert found.recent_news == "Raised a round."


def test_upsert_company_profile_replaces_not_appends(session):
    repo.upsert_company_profile(
        session, display_name="Acme", summary="old", industry=None, company_size=None,
        headquarters=None, website=None, recent_news=None, source_urls=None,
    )
    repo.upsert_company_profile(
        session, display_name="Acme", summary="new", industry=None, company_size=None,
        headquarters=None, website=None, recent_news=None, source_urls=None,
    )

    profile = repo.get_company_profile(session, "Acme")
    assert profile.summary == "new"


# --- endpoint tests (POST /api/applications/{id}/research) ---


def _make_application(client):
    return repo.create_application(
        client.db_session, company_name="EGYM", job_title="Backend Engineer", platform="other",
        applied_date=date(2026, 1, 1), current_status="applied",
    )


def test_research_endpoint_researches_and_caches(client):
    application = _make_application(client)
    profile_result = CompanyProfileResult(
        summary="Fitness technology company.", industry="Fitness technology", website="https://egym.com",
    )
    client.app.dependency_overrides[get_search_client] = lambda: FakeSearchClient(results=_results())
    client.app.dependency_overrides[get_llm_model] = lambda: _model_returning(profile_result)

    response = client.post(f"/api/applications/{application.id}/research")

    assert response.status_code == 200
    body = response.json()
    assert body["company_name"] == "EGYM"
    assert body["summary"] == "Fitness technology company."
    assert body["source_urls"] == ["https://egym.com", "https://news.example/egym-series-f"]
    # It was cached.
    assert repo.get_company_profile(client.db_session, "EGYM") is not None


def test_research_endpoint_returns_cache_without_researching(client):
    application = _make_application(client)
    repo.upsert_company_profile(
        client.db_session, display_name="EGYM", summary="cached summary", industry=None,
        company_size=None, headquarters=None, website=None, recent_news=None, source_urls=None,
    )
    # Search client that would blow up if actually called - proves the cache path.
    client.app.dependency_overrides[get_search_client] = lambda: FakeSearchClient(
        exception=SearxngError("should not be called")
    )
    client.app.dependency_overrides[get_llm_model] = lambda: FakeCompletionModel()

    response = client.post(f"/api/applications/{application.id}/research")

    assert response.status_code == 200
    assert response.json()["summary"] == "cached summary"


def test_research_endpoint_refresh_bypasses_cache(client):
    application = _make_application(client)
    repo.upsert_company_profile(
        client.db_session, display_name="EGYM", summary="stale", industry=None, company_size=None,
        headquarters=None, website=None, recent_news=None, source_urls=None,
    )
    client.app.dependency_overrides[get_search_client] = lambda: FakeSearchClient(results=_results())
    client.app.dependency_overrides[get_llm_model] = lambda: _model_returning(
        CompanyProfileResult(summary="fresh")
    )

    response = client.post(f"/api/applications/{application.id}/research?refresh=true")

    assert response.status_code == 200
    assert response.json()["summary"] == "fresh"


def test_research_endpoint_404_for_missing_application(client):
    client.app.dependency_overrides[get_search_client] = lambda: FakeSearchClient()
    client.app.dependency_overrides[get_llm_model] = lambda: FakeCompletionModel()

    response = client.post("/api/applications/9999/research")

    assert response.status_code == 404


def test_research_endpoint_502_on_search_failure(client):
    application = _make_application(client)
    client.app.dependency_overrides[get_search_client] = lambda: FakeSearchClient(
        exception=SearxngError("down")
    )
    client.app.dependency_overrides[get_llm_model] = lambda: FakeCompletionModel()

    response = client.post(f"/api/applications/{application.id}/research")

    assert response.status_code == 502
    # Plain-language message, not the raw exception.
    assert "could not research" in response.json()["detail"].lower()
