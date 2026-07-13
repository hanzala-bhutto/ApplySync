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


def test_build_search_query_includes_invitation_phrases_as_full_text():
    """invitation_phrases must be searched across the whole email (no subject:
    prefix), since interview invites carry no application keyword in the
    subject - that's the whole point of them."""
    query = build_search_query(get_sources())
    assert '"invitation to a first conversation"' in query
    # full-text, NOT subject-scoped
    assert 'subject:"invitation to a first conversation"' not in query


def test_build_search_query_handles_config_without_invitation_phrases():
    """The field defaults to empty, so an older sources config still builds."""
    from applysync.config import SourcesConfig

    sources = SourcesConfig(confirmation_keywords=["applied"], platforms=[])
    query = build_search_query(sources)
    assert query == "(subject:applied)"


def test_guess_platform_matches_known_domain():
    assert guess_platform("jobs-noreply@linkedin.com", get_sources()) == "linkedin"


def test_guess_platform_matches_ats_vendor_domain():
    assert guess_platform("notification@smartrecruiters.com", get_sources()) == "smartrecruiters"


def test_guess_platform_returns_none_for_direct_company_domain():
    assert guess_platform("careers@somecompany.example", get_sources()) is None
