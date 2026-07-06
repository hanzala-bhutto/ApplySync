from __future__ import annotations

import base64
import html
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

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
    """Prefer text/plain anywhere in the MIME tree; fall back to text/html
    (tag-stripped) for HTML-only emails, which several platforms send.
    """
    plain_part = _find_part(payload, "text/plain")
    if plain_part is not None:
        return _decode(plain_part["body"]["data"], _charset_of(plain_part))

    html_part = _find_part(payload, "text/html")
    if html_part is not None:
        return _strip_html(_decode(html_part["body"]["data"], _charset_of(html_part)))

    return ""


def _find_part(payload: dict, mime_type: str) -> dict | None:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return payload
    for part in payload.get("parts") or []:
        found = _find_part(part, mime_type)
        if found is not None:
            return found
    return None


def _charset_of(part: dict) -> str:
    """Read the charset out of the part's own Content-Type header. Gmail API
    parts do not always inherit UTF-8; several platforms (e.g. StepStone) send
    Windows-1252/Latin-1 bodies, which otherwise decode to replacement chars.
    """
    for header in part.get("headers") or []:
        if header.get("name", "").lower() == "content-type":
            match = re.search(r'charset="?([\w-]+)"?', header.get("value", ""), re.IGNORECASE)
            if match:
                return match.group(1)
    return "utf-8"


def _decode(data: str, charset: str = "utf-8") -> str:
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _strip_html(markup: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", markup, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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

    def get_message(self, message_id: str) -> RawEmail:
        """Fetch and parse a single message by id, e.g. to refetch the email
        behind a stored `source_email_id` (reprocessing, showing the source
        email for human verification).
        """
        raw_message = self.service.users().messages().get(userId="me", id=message_id, format="full").execute()
        return parse_message(raw_message)

    def fetch_messages(self, query: str, max_results: int = 500) -> list[RawEmail]:
        """max_results is a total cap across ALL pages, not a per-page size
        (Gmail's list API caps each page at 100 regardless). Gmail's list
        endpoint is paginated via nextPageToken; a single un-paginated call
        silently misses everything past the first page. Confirmed against a
        real inbox with 238+ applications: a single maxResults=50 call left
        nextPageToken set, meaning most matching emails were never fetched.
        """
        refs: list[dict] = []
        page_token: str | None = None
        while len(refs) < max_results:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(100, max_results - len(refs)),
                    pageToken=page_token,
                )
                .execute()
            )
            refs.extend(response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return self._fetch_message_bodies([ref["id"] for ref in refs[:max_results]])

    def _fetch_message_bodies(self, message_ids: list[str]) -> list[RawEmail]:
        """Fetch each message's full body concurrently. 10 workers is
        deliberately conservative: the LLM's 40rpm rate limit is the real
        bottleneck for a full sync, so faster fetching doesn't shorten
        wall-clock time much - there is no benefit to pushing this higher.

        A failed per-message fetch (network blip, transient error) is
        logged and excluded rather than aborting the whole batch; it is
        never marked processed, so it naturally gets retried next sync.
        """

        def fetch_one(message_id: str) -> RawEmail | None:
            try:
                service = self._service_for_current_thread()
                full = (
                    service.users().messages().get(userId="me", id=message_id, format="full").execute()
                )
                return parse_message(full)
            except Exception:
                logger.warning(
                    "Failed to fetch message %s, skipping - will retry next sync",
                    message_id,
                    exc_info=True,
                )
                return None

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(fetch_one, message_ids))
        return [email for email in results if email is not None]

    def _service_for_current_thread(self):
        """googleapiclient's underlying httplib2 transport is not documented
        as safe to share across threads, so each worker thread gets its own
        service instance built from the same credentials rather than
        reusing self.service. Test doubles that preset self._service (no
        real Settings) skip this and share the fake directly - thread
        safety isn't a concern for a fake in-memory object.
        """
        if getattr(self, "_settings", None) is None:
            return self.service
        thread_local = self.__dict__.setdefault("_thread_local", threading.local())
        if not hasattr(thread_local, "service"):
            creds = load_credentials(self._settings)
            thread_local.service = build("gmail", "v1", credentials=creds)
        return thread_local.service
