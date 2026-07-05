from __future__ import annotations

import base64
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from applysync.config import Settings
from applysync.gmail.models import RawEmail

logger = logging.getLogger(__name__)

# Readonly only. Never widen this without updating CLAUDE.md's hard constraints.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_credentials(settings: Settings) -> Credentials:
    token_path = settings.gmail_token_path
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(settings.gmail_client_secrets_path), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return creds


def parse_message(message: dict) -> RawEmail:
    """Turn a Gmail API 'full' format message resource into a RawEmail.
    Kept separate from the API call itself so it can be unit tested against
    fixture payloads without real credentials.
    """
    headers = {h["name"].lower(): h["value"] for h in message["payload"].get("headers", [])}
    return RawEmail(
        message_id=message["id"],
        thread_id=message["threadId"],
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        date=headers.get("date", ""),
        body=_extract_body(message["payload"]),
    )


def _extract_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])

    parts = payload.get("parts") or []
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode(part["body"]["data"])
    for part in parts:
        text = _extract_body(part)
        if text:
            return text
    return ""


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


class GmailClient:
    """Thin wrapper over the Gmail API. Requires real OAuth credentials
    (see the /gmail-setup skill) so it is exercised manually via
    scripts/gmail_probe.py, not in the automated test suite.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._service = None

    @property
    def service(self):
        if self._service is None:
            creds = load_credentials(self._settings)
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def fetch_messages(self, query: str, max_results: int = 50) -> list[RawEmail]:
        response = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        emails: list[RawEmail] = []
        for ref in response.get("messages", []):
            full = (
                self.service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            emails.append(parse_message(full))
        return emails
