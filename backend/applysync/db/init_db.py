from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from alembic import command
from alembic.config import Config
from applysync.config import PROJECT_ROOT, get_settings
from applysync.db import models  # noqa: F401  (registers tables with SQLModel metadata)
from applysync.db.models import PipelineRun, ReviewSuggestion

# The baseline migration (alembic/versions/0001_baseline.py) captures the whole
# current schema. Everything after it is a normal Alembic migration.
_BASELINE_REVISION = "0001"

# FROZEN pre-Alembic column bridge. Some local databases were created before
# certain nullable/defaulted columns existed and were kept current by a
# hand-rolled ALTER TABLE pass. Alembic now owns all schema changes GOING
# FORWARD; this map exists only to bring such a database up to the 0001 baseline
# before it is stamped, so no historical local db is left missing a column. Do
# NOT extend it - new columns ship as a migration (`applysync db revision`).
_LEGACY_BRIDGE_COLUMNS: dict[type, dict[str, str]] = {
    PipelineRun: {
        "emails_total": "INTEGER",
        "emails_scrutinized": "INTEGER NOT NULL DEFAULT 0",
        "emails_extracted": "INTEGER NOT NULL DEFAULT 0",
        "emails_written": "INTEGER NOT NULL DEFAULT 0",
        "updated_at": "DATETIME",
        "run_type": "VARCHAR NOT NULL DEFAULT 'incremental'",
        "suggestions_created": "INTEGER NOT NULL DEFAULT 0",
    },
    ReviewSuggestion: {
        "confidence": "VARCHAR",
    },
}


def get_engine(db_path: Path | None = None):
    path = db_path or get_settings().db_path
    return create_engine(f"sqlite:///{path}")


def _alembic_config(db_path: Path) -> Config:
    """An Alembic Config pointed at db_path. The URL is set here (not in
    alembic.ini) so init_db, the CLI, and the tests all drive the same
    migration scripts against whichever database file they mean."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _bridge_legacy_columns(engine) -> None:
    """Add any 0001-baseline columns a pre-Alembic database is still missing
    (SQLite ALTER TABLE ADD COLUMN). No-op once the columns exist and no-op on a
    fresh database. See _LEGACY_BRIDGE_COLUMNS - frozen, not the go-forward
    mechanism."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    for model, columns in _LEGACY_BRIDGE_COLUMNS.items():
        if model.__tablename__ not in table_names:
            continue
        existing = {col["name"] for col in inspector.get_columns(model.__tablename__)}
        missing = {name: ddl for name, ddl in columns.items() if name not in existing}
        if not missing:
            continue
        with engine.begin() as connection:
            for name, ddl_type in missing.items():
                connection.execute(
                    text(f"ALTER TABLE {model.__tablename__} ADD COLUMN {name} {ddl_type}")
                )


def init_db(db_path: Path | None = None) -> None:
    """Bring the database at db_path to the latest schema via Alembic.

    - Already Alembic-managed (has an alembic_version table): `upgrade head`.
    - Pre-Alembic database with real data (has app tables, no alembic_version):
      add any missing tables/columns to reach the 0001 baseline, `stamp` it
      there, then `upgrade head` - adopting the existing data without ever
      recreating or dropping it.
    - Fresh/empty database: `upgrade head` builds the whole schema from the
      migration scripts.
    """
    path = db_path or get_settings().db_path
    cfg = _alembic_config(path)

    engine = get_engine(path)
    try:
        table_names = set(inspect(engine).get_table_names())
        # Legacy = any of our tables already exist but Alembic has never managed
        # this file. Keyed on the full model set (not just `application`) so a db
        # holding any subset of pre-Alembic tables is adopted, not recreated.
        known_tables = set(SQLModel.metadata.tables)
        is_legacy = bool(known_tables & table_names) and "alembic_version" not in table_names
        if is_legacy:
            SQLModel.metadata.create_all(engine)  # add any fully-missing tables
            _bridge_legacy_columns(engine)  # add any missing baseline columns
    finally:
        # Release our connection to the SQLite file before Alembic opens its
        # own for DDL (avoids a write-lock conflict, notably on Windows).
        engine.dispose()

    if is_legacy:
        command.stamp(cfg, _BASELINE_REVISION)
    command.upgrade(cfg, "head")


def get_session(db_path: Path | None = None) -> Session:
    return Session(get_engine(db_path))
