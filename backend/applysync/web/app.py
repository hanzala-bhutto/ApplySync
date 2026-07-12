from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from applysync.config import get_settings
from applysync.db.init_db import get_engine, init_db
from applysync.gmail.client import GmailClient
from applysync.llm import get_chat_model
from applysync.web.api import register_api_routes
from applysync.web.gmail_oauth import register_gmail_oauth_routes
from applysync.web.review import register_review_routes
from applysync.web.sync import register_sync_routes

# No handler was ever configured for this app's own loggers (gmail/client.py,
# pipeline/nodes.py, web/sync.py all call logging.getLogger(__name__) but
# nothing attached a handler to the root logger) - warnings/errors logged
# anywhere in the app were silently going nowhere instead of the server
# terminal. basicConfig is a no-op if a handler already exists, so this is
# safe to call unconditionally at import time.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# The React frontend (frontend/) and this API run as two separate servers
# (by design, not just during development), so CORS is needed rather than
# optional. Matched by regex, not a fixed port: Vite falls back to the next
# free port whenever another project's dev server already holds 5173, which
# is common enough on a dev machine that hardcoding one port would break
# this unpredictably.
_FRONTEND_DEV_ORIGIN_REGEX = r"http://(localhost|127\.0\.0\.1):\d+"


def get_session():
    settings = get_settings()
    init_db(settings.db_path)
    with Session(get_engine(settings.db_path)) as session:
        yield session


def get_gmail_client() -> GmailClient:
    return GmailClient(get_settings())


def get_llm_model():
    return get_chat_model(get_settings())


def create_app() -> FastAPI:
    app = FastAPI(
        title="ApplySync API",
        description="Job application tracker: Gmail-ingested, LLM-extracted, "
        "browsable and correctable here. See the React frontend (frontend/) "
        "for the actual dashboard UI - this is the API it talks to.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_FRONTEND_DEV_ORIGIN_REGEX,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_api_routes(app, get_session=get_session, get_gmail_client=get_gmail_client, get_llm_model=get_llm_model)
    register_gmail_oauth_routes(app, get_settings=get_settings)
    register_sync_routes(app, get_session=get_session, get_settings=get_settings)
    register_review_routes(app, get_session=get_session)

    return app


app = create_app()
