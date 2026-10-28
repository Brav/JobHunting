import pytest
from jobhunt.db import init_db, make_engine, make_session_factory


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return engine


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)
