from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from applysync.config import get_settings
from applysync.db import models  # noqa: F401  (registers tables with SQLModel metadata)


def get_engine(db_path: Path | None = None):
    path = db_path or get_settings().db_path
    return create_engine(f"sqlite:///{path}")


def init_db(db_path: Path | None = None) -> None:
    SQLModel.metadata.create_all(get_engine(db_path))


def get_session(db_path: Path | None = None) -> Session:
    return Session(get_engine(db_path))
