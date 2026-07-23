"""Alembic environment for ApplySync.

Design notes specific to this project:
- `target_metadata` is `SQLModel.metadata`. Importing `applysync.db.models`
  registers every table on it, so autogenerate diffs the live database against
  the current models.
- The URL defaults to `settings.db_path` (the same .env-driven path the app
  uses) but can be overridden per run via the config's `sqlalchemy.url` main
  option - that's how `init_db` and the tests target a specific database file.
- `render_as_batch=True`: SQLite cannot do most `ALTER TABLE`s in place, so
  Alembic must rebuild the table via a temporary copy ("batch" mode). Without
  this, anything beyond `ADD COLUMN` fails on SQLite.
- `include_name` restricts Alembic to the tables our models define (plus its
  own `alembic_version`). The LangGraph `SqliteSaver` checkpointer writes its
  own tables into the SAME `applysync.db`; without this filter, autogenerate
  would see them as unknown and emit `drop_table` for them.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from alembic import context
from applysync.config import get_settings
from applysync.db import models  # noqa: F401  (registers tables on SQLModel.metadata)

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: init_db runs Alembic in-process on every
    # sync, and the default (True) would tear down the app's own loggers each
    # time.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata

# Tables Alembic is allowed to manage: our own, plus its version bookkeeping.
_MANAGED_TABLES = set(target_metadata.tables) | {"alembic_version"}


def _database_url() -> str:
    override = config.get_main_option("sqlalchemy.url")
    if override:
        return override
    return f"sqlite:///{get_settings().db_path}"


def include_name(name, type_, parent_names):
    if type_ == "table":
        return name in _MANAGED_TABLES
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
