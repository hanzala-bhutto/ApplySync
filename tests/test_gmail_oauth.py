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
    settings.gmail_token_path.write_text(
        json.dumps(
            {
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
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
