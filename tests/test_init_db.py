import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from applysync.db import repository as repo
from applysync.db.init_db import get_engine, get_session, init_db
from applysync.db.models import Application, PipelineRun, ReviewSuggestion


def _make_application(session, company="Acme"):
    return repo.create_application(
        session,
        company_name=company,
        job_title="Engineer",
        platform="linkedin",
        applied_date=date(2026, 1, 1),
        current_status="applied",
    )


def _alembic_version(db_path: Path) -> str | None:
    engine = get_engine(db_path)
    if "alembic_version" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


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
        assert run.run_type == "incremental"
        assert run.suggestions_created == 0


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
    assert {
        "emails_total",
        "emails_scrutinized",
        "emails_extracted",
        "emails_written",
        "updated_at",
        "run_type",
        "suggestions_created",
    } <= columns

    with get_session(db_path) as session:
        old_run = session.get(PipelineRun, "old-run")
        assert old_run is not None
        assert old_run.emails_fetched == 5
        assert old_run.emails_scrutinized == 0
        assert old_run.emails_extracted == 0
        assert old_run.emails_written == 0
        assert old_run.emails_total is None
        assert old_run.run_type == "incremental"
        assert old_run.suggestions_created == 0


def test_init_db_migrates_existing_review_suggestion_missing_confidence(tmp_path: Path):
    """A pre-existing reviewsuggestion table (created before the confidence-
    routed-merge feature) is missing the confidence column: init_db must add
    it via ALTER TABLE without losing existing suggestion rows.
    """
    db_path = tmp_path / "pre_existing.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE reviewsuggestion (
            id INTEGER NOT NULL PRIMARY KEY,
            message_id VARCHAR NOT NULL,
            application_id INTEGER,
            action VARCHAR NOT NULL,
            previous_classification VARCHAR NOT NULL,
            suggested_classification VARCHAR NOT NULL,
            previous_extract_json VARCHAR,
            suggested_extract_json VARCHAR,
            status VARCHAR NOT NULL,
            pipeline_run_id VARCHAR NOT NULL,
            created_at DATETIME NOT NULL,
            reviewed_at DATETIME
        )
        """
    )
    conn.execute(
        "INSERT INTO reviewsuggestion (id, message_id, action, previous_classification, "
        "suggested_classification, status, pipeline_run_id, created_at) VALUES "
        "(1, 'old-msg', 'new_application', 'irrelevant', 'relevant', 'pending', 'old-run', "
        "'2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    from sqlalchemy import inspect

    columns = {col["name"] for col in inspect(get_engine(db_path)).get_columns(ReviewSuggestion.__tablename__)}
    assert "confidence" in columns

    with get_session(db_path) as session:
        old = session.get(ReviewSuggestion, 1)
        assert old is not None
        assert old.message_id == "old-msg"
        assert old.confidence is None


# --- Alembic management -----------------------------------------------------


def test_init_db_fresh_database_is_alembic_managed_at_baseline(tmp_path: Path):
    """A fresh database is built by the migrations and left recorded at the
    baseline revision, so future `upgrade` calls know where it stands."""
    db_path = tmp_path / "fresh.db"
    init_db(db_path)

    assert _alembic_version(db_path) == "0001"


def test_init_db_is_idempotent_and_preserves_data(tmp_path: Path):
    """Calling init_db again (every sync does) is a no-op that never errors and
    never touches existing rows."""
    db_path = tmp_path / "repeat.db"
    init_db(db_path)
    with get_session(db_path) as session:
        _make_application(session)

    init_db(db_path)  # second call

    assert _alembic_version(db_path) == "0001"
    with get_session(db_path) as session:
        assert len(session.exec(select(Application)).all()) == 1


def test_init_db_adopts_pre_alembic_database_without_recreating(tmp_path: Path):
    """The real pre-Alembic applysync.db case: a database that already holds the
    full current schema (via create_all) but has never been Alembic-managed.
    init_db must stamp it in place - not try to recreate its tables (which would
    error) - and keep every row."""
    db_path = tmp_path / "legacy_full.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _make_application(session, company="LegacyCo")
    engine.dispose()

    # No alembic_version yet: this is a pre-Alembic database.
    assert _alembic_version(db_path) is None

    init_db(db_path)  # adopt

    assert _alembic_version(db_path) == "0001"
    with get_session(db_path) as session:
        rows = session.exec(select(Application)).all()
        assert len(rows) == 1
        assert rows[0].company_name == "LegacyCo"
