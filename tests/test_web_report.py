from datetime import datetime

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.runner import run_daily
from jobhunt.sources.base import SourceBlocked


def seed(app, when, outcomes):
    clock = FakeClock(when)
    run_daily(app.state.session_factory, FakeSource(outcomes), trigger="manual", clock=clock, sleep=clock.sleep)


def test_empty_state(app_factory):
    with TestClient(app_factory()) as c:
        assert "No reports yet." in c.get("/").text


def test_report_page_and_navigation(app_factory):
    app = app_factory()
    sf = app.state.session_factory
    add_listing(sf, "Python BG", keywords="python")
    add_listing(sf, "Data Eng Berlin", keywords="data")
    seed(app, datetime(2026, 10, 26, 9, 0), {"python": [[p("1", title="Old Job")]], "data": [[]]})
    seed(app, datetime(2026, 10, 28, 9, 0), {"python": [[p("2", title="Django Dev")]],
                                              "data": [SourceBlocked("rate limited by LinkedIn")]})
    with TestClient(app) as c:
        home = c.get("/").text
        assert "Django Dev" in home and "Old Job" not in home
        assert f'href="/jobs/{job_id(sf, "2")}"' in home
        assert "rate limited by LinkedIn" in home
        assert 'href="/report/2026-10-26"' in home and "2026-10-26" in home
        old = c.get("/report/2026-10-26").text
        assert "Old Job" in old and "No new jobs." in old and 'href="/report/2026-10-28"' in old
        assert "No search ran on this day." in c.get("/report/2026-10-27").text
        r = c.get("/report", params={"day": "2026-10-26"}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/report/2026-10-26")


def test_job_status(app_factory):
    app = app_factory()
    add_listing(app.state.session_factory, "Python BG", keywords="python")
    seed(app, datetime(2026, 10, 28, 9, 0), {"python": [[p("1", title="Django Dev")]]})
    jid = job_id(app.state.session_factory, "1")
    with TestClient(app) as c:
        r = c.post(f"/jobs/{jid}/status", data={"status": "interested"})
        assert r.status_code == 200 and f'id="job-{jid}"' in r.text and "Reset" in r.text
        r = c.post(f"/jobs/{jid}/status", data={"status": "hidden"})
        assert (r.status_code, r.text) == (200, "")
        assert "Django Dev" not in c.get("/report/2026-10-28").text
        assert c.post(f"/jobs/{jid}/status", data={"status": "bogus"}).status_code == 400
        assert c.post("/jobs/9999/status", data={"status": "applied"}).status_code == 404


def test_report_jump_without_a_date_goes_home(app_factory):
    with TestClient(app_factory()) as c:
        for day in ("", "not-a-date"):
            r = c.get("/report", params={"day": day}, follow_redirects=False)
            assert (r.status_code, r.headers["location"]) == (303, "/")


def test_report_shows_when_a_search_hit_the_limit(app_factory):
    app = app_factory()
    add_listing(app.state.session_factory, "Python BG", keywords="python")
    add_listing(app.state.session_factory, "Go BG", keywords="go")
    seed(app, datetime(2026, 10, 28, 9, 0), {"python": [[p(str(i)) for i in range(100)]], "go": [[p("x")]]})
    with TestClient(app) as c:
        assert c.get("/report/2026-10-28").text.count("hit the limit of 100 jobs") == 1
