from datetime import date

from applysync.config import get_sources
from applysync.gmail.query_builder import build_search_query, guess_platform


def test_build_search_query_uses_confirmation_keywords_not_sender_domains():
    query = build_search_query(get_sources())
    assert "subject:" in query
    assert "from:" not in query


def test_build_search_query_quotes_multiword_keywords():
    query = build_search_query(get_sources())
    assert 'subject:"thank you for applying"' in query


def test_build_search_query_adds_after_bound_when_given():
    query = build_search_query(get_sources(), after=date(2026, 1, 1))
    assert "after:2026/01/01" in query


def test_build_search_query_omits_after_bound_when_not_given():
    query = build_search_query(get_sources())
    assert "after:" not in query


def test_build_search_query_includes_broadened_single_word_keywords():
    query = build_search_query(get_sources())
    assert "subject:applied" in query
    assert "subject:rejected" in query
    assert "subject:interview" in query


def test_guess_platform_matches_known_domain():
    assert guess_platform("jobs-noreply@linkedin.com", get_sources()) == "linkedin"


def test_guess_platform_matches_ats_vendor_domain():
    assert guess_platform("notification@smartrecruiters.com", get_sources()) == "smartrecruiters"


def test_guess_platform_returns_none_for_direct_company_domain():
    assert guess_platform("careers@somecompany.example", get_sources()) is None
