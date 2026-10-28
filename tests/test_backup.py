import io
import sqlite3
import zipfile
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.backup import backup_now, backups, ensure_recent_backup, export_zip, restore
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.runner import run_daily

NOW = datetime(2026, 10, 28, 9, 0)
ENGINES = []


def database(tmp_path, *ids):
    db = tmp_path / "jobhunt.db"
    engine = make_engine(db)
    init_db(engine)
    sf = make_session_factory(engine)
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(sf, FakeSource({"python": [[p(i) for i in ids]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    ENGINES.append(engine)
    return db, sf


def count_jobs(path):
    with sqlite3.connect(path) as c:
        return c.execute("select count(*) from jobs").fetchone()[0]


def test_backup_and_prune(tmp_path):
    db, _ = database(tmp_path, "1", "2")
    first = backup_now(db, tmp_path / "backups", NOW)
    assert first.name == "jobhunt-2026-10-28-0900.db" and count_jobs(first) == 2
    for i in range(1, 17):
        backup_now(db, tmp_path / "backups", NOW + timedelta(hours=i))
    names = [b.name for b in backups(tmp_path / "backups")]
    assert len(names) == 14 and names[0] == "jobhunt-2026-10-29-0100.db"


def test_recent_backup_only_when_stale(tmp_path):
    db, _ = database(tmp_path, "1")
    assert ensure_recent_backup(db, tmp_path / "backups", NOW) is not None
    assert ensure_recent_backup(db, tmp_path / "backups", NOW + timedelta(hours=23)) is None
    assert ensure_recent_backup(db, tmp_path / "backups", NOW + timedelta(hours=25)) is not None


def test_export_zip(tmp_path):
    db, sf = database(tmp_path, "1")
    with sf() as s:
        data = export_zip(db, s, NOW)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["applications.csv", "jobhunt.db", "jobs.csv"]
        assert z.read("jobs.csv").decode().startswith("id,title,")
        assert z.read("applications.csv").decode().startswith("job_id,company,")


def test_restore_keeps_the_current_database(tmp_path):
    db, _ = database(tmp_path, "1", "2")
    other, _ = database(tmp_path / "other", "9")
    for engine in ENGINES:  # restoring is done with the app stopped
        engine.dispose()
    restore(db, other, now=NOW)
    assert count_jobs(db) == 1
    aside = list(tmp_path.glob("jobhunt.db.before-restore-*"))
    assert len(aside) == 1 and count_jobs(aside[0]) == 2


def test_data_page_and_downloads(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/data/backup", follow_redirects=False)
        assert r.status_code == 303
        page = c.get("/data").text
        name = backups(app.state.settings.db_path.parent / "backups")[0].name
        assert name in page and "Export" in page
        assert c.get(f"/data/backups/{name}").status_code == 200
        assert c.get("/data/backups/..%2Fweb.db").status_code == 404
        assert c.get("/data/jobs.csv").text.startswith("id,title,")
        assert c.get("/data/export.zip").headers["content-type"] == "application/zip"
        assert 'href="/data"' in c.get("/settings").text
