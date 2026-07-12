from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from applysync.config import get_settings
from applysync.db import models  # noqa: F401  (registers tables with SQLModel metadata)
from applysync.db.models import PipelineRun


def get_engine(db_path: Path | None = None):
    path = db_path or get_settings().db_path
    return create_engine(f"sqlite:///{path}")


# column name -> SQLite type + default, for ALTER TABLE ADD COLUMN on an
# existing pipelinerun table. Keep in sync with new nullable/defaulted
# fields added to PipelineRun in models.py; `create_all()` already handles
# these on a brand-new database, this is only for one that predates them.
_PIPELINE_RUN_ADDITIVE_COLUMNS = {
    "emails_total": "INTEGER",
    "emails_scrutinized": "INTEGER NOT NULL DEFAULT 0",
    "emails_extracted": "INTEGER NOT NULL DEFAULT 0",
    "emails_written": "INTEGER NOT NULL DEFAULT 0",
    "updated_at": "DATETIME",
    "run_type": "VARCHAR NOT NULL DEFAULT 'incremental'",
}


def _migrate_pipeline_run_columns(engine) -> None:
    """SQLite supports ALTER TABLE ... ADD COLUMN for simple additions.
    create_all() only creates missing tables, never alters existing ones -
    this closes that gap for an existing local applysync.db so a schema
    change never requires deleting real data. No-op on a brand-new DB
    (already has the full schema) and no-op once columns exist.
    """
    inspector = inspect(engine)
    if PipelineRun.__tablename__ not in inspector.get_table_names():
        return  # brand-new DB, create_all() above already made the full table

    existing_columns = {col["name"] for col in inspector.get_columns(PipelineRun.__tablename__)}
    missing = {
        name: ddl_type
        for name, ddl_type in _PIPELINE_RUN_ADDITIVE_COLUMNS.items()
        if name not in existing_columns
    }
    if not missing:
        return

    with engine.begin() as connection:
        for name, ddl_type in missing.items():
            connection.execute(text(f"ALTER TABLE {PipelineRun.__tablename__} ADD COLUMN {name} {ddl_type}"))


def init_db(db_path: Path | None = None) -> None:
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine)
    _migrate_pipeline_run_columns(engine)


def get_session(db_path: Path | None = None) -> Session:
    return Session(get_engine(db_path))
