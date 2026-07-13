import json

from applysync.config import Settings, get_settings


def _override_settings(client, tmp_path, *, with_secrets: bool = False):
    secrets_path = tmp_path / "credentials.json"
    if with_secrets:
        secrets_path.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "fake-client-id.apps.googleusercontent.com",
                        "client_secret": "fake-client-secret",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"],
                    }
                }
            )
        )
    settings = Settings(gmail_client_secrets_path=secrets_path, gmail_token_path=tmp_path / "token.json")
    client.app.dependency_overrides[get_settings] = lambda: settings
    return settings


def test_gmail_status_not_connected_when_no_token(client, tmp_path):
    _override_settings(client, tmp_path)

    response = client.get("/api/gmail/status")

    assert response.status_code == 200
    assert response.json() == {"connected": False}


def test_gmail_status_connected_when_valid_token_exists(client, tmp_path):
    settings = _override_settings(client, tmp_path)
    # A far-future expiry makes this a genuinely valid (unexpired) token. A
    # google-auth token with no expiry at all is treated as already expired,
    # so it must carry a real future expiry to test the valid path.
    settings.gmail_token_path.write_text(
        json.dumps(
            {
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                "expiry": "2999-01-01T00:00:00.000000Z",
            }
        )
    )

    response = client.get("/api/gmail/status")

    assert response.status_code == 200
    assert response.json() == {"connected": True}


def test_gmail_status_not_connected_on_corrupt_token_file(client, tmp_path):
    settings = _override_settings(client, tmp_path)
    settings.gmail_token_path.write_text("not valid json")

    response = client.get("/api/gmail/status")

    assert response.status_code == 200
    assert response.json() == {"connected": False}


def _expired_token_json():
    return json.dumps(
        {
            "token": "old-access-token",
            "refresh_token": "fake-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "fake-client-id",
            "client_secret": "fake-client-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "expiry": "2020-01-01T00:00:00.000000Z",
        }
    )


def test_gmail_status_disconnected_when_token_revoked(client, tmp_path, monkeypatch):
    """Regression: a revoked/expired refresh token still sits in the file, so
    presence alone must NOT report connected - otherwise the reconnect banner
    never shows and the background sync fails with invalid_grant."""
    settings = _override_settings(client, tmp_path)
    settings.gmail_token_path.write_text(_expired_token_json())

    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    def _raise(self, request):
        raise RefreshError("Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", _raise)

    response = client.get("/api/gmail/status")

    assert response.status_code == 200
    assert response.json() == {"connected": False}


def test_gmail_status_refreshes_and_persists_expired_but_refreshable_token(client, tmp_path, monkeypatch):
    """An expired token that CAN still be refreshed reports connected, and the
    refreshed token is persisted so the next call/sync reuses it."""
    settings = _override_settings(client, tmp_path)
    settings.gmail_token_path.write_text(_expired_token_json())

    from google.oauth2.credentials import Credentials

    def _refresh(self, request):
        self.token = "new-access-token"
        self.expiry = None  # clears expiry -> creds.valid becomes True

    monkeypatch.setattr(Credentials, "refresh", _refresh)

    response = client.get("/api/gmail/status")

    assert response.status_code == 200
    assert response.json() == {"connected": True}
    saved = json.loads(settings.gmail_token_path.read_text())
    assert saved["token"] == "new-access-token"


def test_gmail_connect_redirects_to_google_when_secrets_exist(client, tmp_path):
    _override_settings(client, tmp_path, with_secrets=True)

    response = client.get("/api/gmail/connect?return_to=/reminders", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://accounts.google.com/")


def test_gmail_connect_fails_without_client_secrets(client, tmp_path):
    _override_settings(client, tmp_path, with_secrets=False)

    response = client.get("/api/gmail/connect")

    assert response.status_code == 500


def test_gmail_callback_rejects_unknown_state(client):
    response = client.get("/api/gmail/callback?code=abc&state=not-a-real-state", follow_redirects=False)

    assert response.status_code == 400
