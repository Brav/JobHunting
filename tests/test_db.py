from datetime import datetime

from sqlmodel import select

from jobhunt.db import init_db, make_engine
from jobhunt.models import Run


def test_tables_and_wal(engine):
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        names = {r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"listings", "jobs", "runs", "run_listings", "matches"} <= names


def test_init_db_marks_running_runs_interrupted(engine, session_factory):
    with session_factory() as s:
        s.add(Run(trigger="scheduled", started_at=datetime(2026, 10, 28, 9), status="running"))
        s.commit()
    init_db(engine)
    with session_factory() as s:
        run = s.exec(select(Run)).one()
    assert (run.status, run.error) == ("failed", "interrupted")
    assert run.finished_at is not None


def test_py_lower_folds_non_ascii(engine):
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT py_lower('ПРОГРАМЕР Zürich')").scalar() == "програмер zürich"


def test_init_db_adds_columns_missing_from_older_databases(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE listings (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                          "keywords VARCHAR NOT NULL, location VARCHAR NOT NULL, radius_km INTEGER NOT NULL, "
                          "active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)")
    init_db(engine)
    with engine.connect() as c:
        columns = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(listings)")}
    assert "search_changed_at" in columns


def test_foreign_keys_enforced(engine):
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
