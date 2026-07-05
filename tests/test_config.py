from applysync.config import get_sources


def test_sources_yaml_loads_expected_platforms():
    sources = get_sources()
    ids = {p.id for p in sources.platforms}
    assert {"linkedin", "indeed", "stepstone", "jackandjill", "smartrecruiters"}.issubset(ids)


def test_sources_yaml_loads_confirmation_keywords():
    sources = get_sources()
    assert "thank you for applying" in sources.confirmation_keywords
    assert "bewerbung" in sources.confirmation_keywords
