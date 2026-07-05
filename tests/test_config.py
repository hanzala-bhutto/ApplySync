from applysync.config import get_sources


def test_sources_yaml_loads_expected_platforms():
    sources = get_sources()
    ids = {p.id for p in sources.platforms}
    assert ids == {"linkedin", "indeed", "stepstone", "jackandjill"}
