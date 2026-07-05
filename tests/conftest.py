import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from applysync.db import models  # noqa: F401  (registers tables with SQLModel metadata)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def client():
    from applysync.web.app import create_app, get_session

    # StaticPool: FastAPI's TestClient runs sync routes on a worker thread,
    # and without a shared single connection, a fresh (tableless) in-memory
    # SQLite DB gets created for that thread instead of reusing this one.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    test_session = Session(engine)

    app = create_app()

    def override_get_session():
        yield test_session

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as c:
        c.db_session = test_session
        yield c

    test_session.close()
