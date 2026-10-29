from datetime import datetime

import pytest

from fakes import FakeSource, FakeTranslator
from jobhunt.config import load_settings
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.eligibility import LEGACY_PROFILE, save_profile
from jobhunt.sources.base import JobPage
from jobhunt.web.app import create_app


def _save_legacy_profile(engine) -> None:
    """Most tests start like an upgraded install: the owner's old default saved as the profile."""
    with make_session_factory(engine)() as s:
        save_profile(s, LEGACY_PROFILE, datetime(2026, 10, 1))


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    _save_legacy_profile(engine)
    return engine


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def fresh_engine(tmp_path):
    """A new install: nothing saved, setup pending."""
    engine = make_engine(tmp_path / "fresh.db")
    init_db(engine)
    return engine


@pytest.fixture
def fresh_session_factory(fresh_engine):
    return make_session_factory(fresh_engine)


@pytest.fixture
def app_factory(tmp_path):
    def make(source=None, fetch_page=None, translator=None, cv_path=None, setup_done=True, env=None):
        db_path = tmp_path / "web.db"
        if setup_done:
            engine = make_engine(db_path)
            init_db(engine)
            _save_legacy_profile(engine)
            engine.dispose()
        settings = load_settings({"JOBHUNT_DB_PATH": str(db_path),
                                  "JOBHUNT_CV_PATH": str(cv_path or tmp_path / "missing.pdf"), **(env or {})})
        return create_app(settings, source=source or FakeSource({}),
                          fetch_page=fetch_page or (lambda external_id: JobPage("Job description.", [])),
                          translator=translator or FakeTranslator(), start_scheduler=False, sleep=lambda s: None)
    return make
