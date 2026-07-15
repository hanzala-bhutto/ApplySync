from applysync.config import Settings
from applysync.observability import get_langfuse_handler


def test_returns_none_when_keys_unset():
    settings = Settings(langfuse_public_key="", langfuse_secret_key="")
    assert get_langfuse_handler(settings) is None


def test_fails_open_when_client_init_raises(monkeypatch):
    settings = Settings(langfuse_public_key="pk-test", langfuse_secret_key="sk-test")

    def _boom(*args, **kwargs):
        raise RuntimeError("langfuse unreachable")

    monkeypatch.setattr("langfuse.Langfuse", _boom)
    assert get_langfuse_handler(settings) is None
