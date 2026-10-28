import pytest

from fakes import FakeSource, FakeTranslator
from jobhunt.config import load_settings
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.sources.base import JobPage
from jobhunt.web.app import create_app


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return engine


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def app_factory(tmp_path):
    def make(source=None, fetch_page=None, translator=None, cv_path=None):
        settings = load_settings({"JOBHUNT_DB_PATH": str(tmp_path / "web.db"),
                                  "JOBHUNT_CV_PATH": str(cv_path or tmp_path / "missing.pdf")})
        return create_app(settings, source=source or FakeSource({}),
                          fetch_page=fetch_page or (lambda external_id: JobPage("Job description.", [])),
                          translator=translator or FakeTranslator(), start_scheduler=False, sleep=lambda s: None)
    return make
