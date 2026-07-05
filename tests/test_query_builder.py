from datetime import date

from applysync.config import get_sources
from applysync.gmail.query_builder import build_search_query, guess_platform


def test_build_search_query_includes_all_platform_domains():
    query = build_search_query(get_sources())
    assert "from:linkedin.com" in query
    assert "from:indeed.com" in query
    assert "from:jackandjill.ai" in query


def test_build_search_query_adds_after_bound_when_given():
    query = build_search_query(get_sources(), after=date(2026, 1, 1))
    assert "after:2026/01/01" in query


def test_build_search_query_omits_after_bound_when_not_given():
    query = build_search_query(get_sources())
    assert "after:" not in query


def test_guess_platform_matches_known_domain():
    assert guess_platform("jobs-noreply@linkedin.com", get_sources()) == "linkedin"


def test_guess_platform_returns_none_for_unknown_sender():
    assert guess_platform("someone@example.com", get_sources()) is None
