from __future__ import annotations

from dataclasses import dataclass

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel

from applysync.config import Settings
from applysync.gmail.client import SCOPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gmail", tags=["gmail-oauth"])


@dataclass
class _PendingAuth:
    return_to: str
    # google-auth-oauthlib defaults to autogenerate_code_verifier=True, so
    # the /connect Flow instance generates a PKCE code_verifier and sends its
    # code_challenge to Google. /callback builds a brand-new Flow instance
    # (separate HTTP request, no shared state) - without carrying this same
    # verifier over, fetch_token() sends none, and Google rejects the
    # exchange with "invalid_grant: Missing code verifier".
    code_verifier: str


# state -> pending auth details. In-memory and single-process is fine here:
# this is a personal, single-user local tool (one FastAPI process, no
# multi-worker deployment), and an entry only needs to survive the few
# seconds between redirecting to Google and Google redirecting back.
_pending_states: dict[str, _PendingAuth] = {}


class GmailStatusResponse(BaseModel):
    connected: bool


def _redirect_uri(request: Request) -> str:
    # Loopback redirect (RFC 8252): Google's "Desktop app" OAuth client type
    # accepts any http://localhost or http://127.0.0.1 redirect URI regardless
    # of the exact one on file (the same mechanism InstalledAppFlow's
    # run_local_server(port=0) relies on) - so the existing credentials.json
    # from the CLI spike works here unchanged, no need for a separate "Web
    # application" OAuth client in Google Cloud Console.
    return str(request.base_url).rstrip("/") + "/api/gmail/callback"


def register_gmail_oauth_routes(app, *, get_settings) -> None:
    """Takes get_settings as a dependency callable (not a direct import),
    same pattern as register_api_routes's get_session/get_gmail_client/
    get_llm_model - lets tests override it via app.dependency_overrides
    instead of touching real on-disk credentials.
    """

    @router.get(
        "/status",
        response_model=GmailStatusResponse,
        summary="Whether a usable Gmail OAuth token is on disk",
    )
    def gmail_status(settings: Settings = Depends(get_settings)):
        if not settings.gmail_token_path.exists():
            return {"connected": False}
        try:
            creds = Credentials.from_authorized_user_file(str(settings.gmail_token_path), SCOPES)
        except (ValueError, OSError):
            return {"connected": False}
        if creds is None:
            return {"connected": False}
        if creds.valid:
            return {"connected": True}
        # Not currently valid. Presence of a refresh_token alone is NOT enough:
        # a revoked or expired refresh token still sits in the file, and
        # reporting it as connected hid the reconnect banner while the
        # background sync failed with invalid_grant. Only truly connected if the
        # token can actually be refreshed - so attempt it and report honestly.
        if not (creds.expired and creds.refresh_token):
            return {"connected": False}
        try:
            creds.refresh(GoogleAuthRequest())
        except RefreshError:
            # Revoked/expired beyond refresh (e.g. the OAuth app is in Testing
            # mode, where refresh tokens expire after 7 days). Report
            # disconnected so the dashboard shows the Connect Gmail banner.
            logger.info("Gmail token present but not refreshable; reporting disconnected")
            return {"connected": False}
        # Refresh succeeded: persist it so the next status check / sync reuses
        # the new access token instead of refreshing again.
        settings.gmail_token_path.write_text(creds.to_json())
        return {"connected": True}

    @router.get(
        "/connect",
        summary="Start the Gmail OAuth flow: redirects to Google's consent screen",
    )
    def gmail_connect(request: Request, return_to: str = "/", settings: Settings = Depends(get_settings)):
        if not settings.gmail_client_secrets_path.exists():
            raise HTTPException(
                status_code=500,
                detail="No Gmail client secrets file found. See the /gmail-setup skill.",
            )
        flow = Flow.from_client_secrets_file(
            str(settings.gmail_client_secrets_path), scopes=SCOPES, redirect_uri=_redirect_uri(request)
        )
        # prompt="consent" forces a fresh refresh_token even on re-consent
        # (e.g. after the user revoked access) - without it, re-authorizing
        # an already-granted app can come back with no refresh_token at all.
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
        _pending_states[state] = _PendingAuth(return_to=return_to, code_verifier=flow.code_verifier)
        return RedirectResponse(auth_url)

    @router.get(
        "/callback",
        summary="Gmail OAuth redirect target: exchanges the code for a token and saves it",
    )
    def gmail_callback(
        request: Request,
        code: str,
        state: str,
        error: str | None = Query(default=None),
        settings: Settings = Depends(get_settings),
    ):
        if state not in _pending_states:
            raise HTTPException(status_code=400, detail="Unknown or expired OAuth state, please try connecting again")
        pending = _pending_states.pop(state)
        return_to = pending.return_to
        separator = "&" if "?" in return_to else "?"

        if error:
            return RedirectResponse(f"{return_to}{separator}gmail=error")

        flow = Flow.from_client_secrets_file(
            str(settings.gmail_client_secrets_path),
            scopes=SCOPES,
            redirect_uri=_redirect_uri(request),
            code_verifier=pending.code_verifier,
        )
        flow.fetch_token(code=code)

        settings.gmail_token_path.parent.mkdir(parents=True, exist_ok=True)
        settings.gmail_token_path.write_text(flow.credentials.to_json())

        return RedirectResponse(f"{return_to}{separator}gmail=connected")

    app.include_router(router)
