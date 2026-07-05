from datetime import date
from pathlib import Path

from sqlmodel import select

from applysync.db import repository as repo
from applysync.db.init_db import get_session, init_db
from applysync.db.models import Application


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
