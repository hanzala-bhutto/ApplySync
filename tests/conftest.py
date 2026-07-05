import pytest
from sqlmodel import Session, SQLModel, create_engine

from applysync.db import models  # noqa: F401  (registers tables with SQLModel metadata)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
