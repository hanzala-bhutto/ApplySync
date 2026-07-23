# Alembic migrations

## Motivation
Schema changes currently mean either deleting the local `applysync.db` (losing 237+ real applications) or hand-editing a per-column `ALTER TABLE` hack in `init_db.py`.

## Problem
`SQLModel.metadata.create_all()` only creates missing tables, never alters existing ones; the `_migrate_additive_columns` workaround only handles `ADD COLUMN` on two hardcoded tables and must be kept in sync with the models by hand - it cannot rename, retype, index, or add constraints.

## Solution
Adopt Alembic (the standard migration tool for SQLAlchemy, which SQLModel already sits on) as the single source of truth for schema evolution: a `0001` baseline capturing the current schema, `init_db` running `alembic upgrade head`, and a one-time stamp path that adopts the existing populated db without recreating it.

## Changes
- `alembic.ini`, `alembic/env.py` (batch mode for SQLite, `SQLModel.metadata` as target, url from settings, ignores the LangGraph checkpointer's own tables in the same db), `alembic/versions/0001_baseline.py`.
- `db/init_db.py`: run migrations instead of `create_all`; stamp a pre-Alembic db at the baseline (with a one-time legacy column bridge for data safety) rather than extending the hardcoded ALTER hack.
- `cli.py`: an `applysync db` command group (`upgrade`, `revision`, `current`, `downgrade`).
- `pyproject.toml`: add `alembic`.

## Benefits
- Any schema change (not just added columns) ships as a reviewable, ordered, reversible migration that never wipes real data.
- Retires the manually-maintained additive-column dicts as the go-forward mechanism.
- Autogenerate diffs models against the live db, so new columns are one command, not hand-written DDL kept in sync by memory.
