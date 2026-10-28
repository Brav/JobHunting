from datetime import date

from fastapi.testclient import TestClient

from fakes import FakeSource, add_listing, make_posting as p, wait_until
from jobhunt.sources.base import SourceBlocked
from jobhunt.web.common import days_ago


def test_status_bar_without_runs(app_factory):
    with TestClient(app_factory()) as client:
        r = client.get("/runs/status")
    assert r.status_code == 200 and "No runs yet." in r.text and "Run now" in r.text


def test_run_now(app_factory):
    app = app_factory(FakeSource({"python": [[p("1")]]}))
    add_listing(app.state.session_factory, "Python BG", keywords="python")
    with TestClient(app) as client:
        assert client.post("/runs").status_code == 200
        wait_until(lambda: not app.state.coordinator.is_running)
        assert "1 of 1 listings OK" in client.get("/runs/status").text


def test_status_bar_shows_failure(app_factory):
    app = app_factory(FakeSource({"data": [SourceBlocked("rate limited by LinkedIn")]}))
    add_listing(app.state.session_factory, "Data Eng Berlin", keywords="data")
    with TestClient(app) as client:
        app.state.coordinator.run_blocking("manual")
        text = client.get("/runs/status").text
    assert "0 of 1 listings OK" in text and "'Data Eng Berlin' failed: rate limited by LinkedIn" in text


def test_static_htmx(app_factory):
    with TestClient(app_factory()) as client:
        assert client.get("/static/htmx.min.js").status_code == 200


def test_days_ago():
    today = date(2026, 10, 28)
    assert [days_ago(d, today) for d in (date(2026, 10, 28), date(2026, 10, 27), date(2026, 10, 25), None)] == [
        "today", "yesterday", "3 days ago", "—"]


def test_translator_choice(tmp_path):
    from jobhunt.config import load_settings
    from jobhunt.translate import DeepLTranslator, NoTranslator
    from jobhunt.web.app import create_app
    base = {"JOBHUNT_DB_PATH": str(tmp_path / "t.db")}
    assert isinstance(create_app(load_settings(base), start_scheduler=False).state.translator, NoTranslator)
    app = create_app(load_settings({**base, "DEEPL_AUTH_KEY": "abc:fx"}), start_scheduler=False)
    assert isinstance(app.state.translator, DeepLTranslator)


def test_status_poll_refreshes_the_page_when_the_run_has_finished(app_factory):
    app = app_factory()
    with TestClient(app) as client:
        app.state.coordinator._lock.acquire()  # a run in progress
        try:
            assert "HX-Refresh" not in client.get("/runs/status").headers
        finally:
            app.state.coordinator._lock.release()
        assert client.get("/runs/status").headers.get("HX-Refresh") == "true"
