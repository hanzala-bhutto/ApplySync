import sqlite3
from datetime import date
from pathlib import Path

from sqlmodel import select

from applysync.db import repository as repo
from applysync.db.init_db import get_engine, get_session, init_db
from applysync.db.models import Application, PipelineRun


def test_init_db_creates_usable_tables(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    with get_session(db_path) as session:
        repo.create_application(
            session,
            company_name="Acme",
            job_title="Engineer",
            platform="linkedin",
            applied_date=date(2026, 1, 1),
            current_status="applied",
        )
        rows = session.exec(select(Application)).all()
        assert len(rows) == 1


def test_init_db_on_fresh_database_includes_pipeline_run_progress_columns(tmp_path: Path):
    db_path = tmp_path / "fresh.db"
    init_db(db_path)

    with get_session(db_path) as session:
        run = repo.create_pipeline_run(session, "run-1")
        assert run.emails_total is None
        assert run.emails_scrutinized == 0
        assert run.emails_extracted == 0
        assert run.emails_written == 0
        assert run.updated_at is not None


def test_init_db_migrates_existing_database_missing_progress_columns(tmp_path: Path):
    """Simulates a real pre-existing applysync.db (created before the
    emails_total/emails_scrutinized/emails_extracted/emails_written/
    updated_at columns existed): a plain sqlite3 connection creates the old-
    shape pipelinerun table and one real row, then init_db must add the
    missing columns via ALTER TABLE without losing that row's data.
    """
    db_path = tmp_path / "pre_existing.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE pipelinerun (
            id VARCHAR NOT NULL PRIMARY KEY,
            started_at DATETIME NOT NULL,
            finished_at DATETIME,
            emails_fetched INTEGER NOT NULL,
            emails_relevant INTEGER NOT NULL,
            applications_created INTEGER NOT NULL,
            events_created INTEGER NOT NULL,
            errors VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO pipelinerun (id, started_at, emails_fetched, emails_relevant, "
        "applications_created, events_created) VALUES ('old-run', '2026-01-01 00:00:00', 5, 3, 2, 2)"
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    engine = get_engine(db_path)
    from sqlalchemy import inspect

    columns = {col["name"] for col in inspect(engine).get_columns(PipelineRun.__tablename__)}
    assert {"emails_total", "emails_scrutinized", "emails_extracted", "emails_written", "updated_at"} <= columns

    with get_session(db_path) as session:
        old_run = session.get(PipelineRun, "old-run")
        assert old_run is not None
        assert old_run.emails_fetched == 5
        assert old_run.emails_scrutinized == 0
        assert old_run.emails_extracted == 0
        assert old_run.emails_written == 0
        assert old_run.emails_total is None
