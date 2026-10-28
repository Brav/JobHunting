import json
from datetime import datetime

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.sources.base import SourceBlocked


def seed(app, when, outcomes):
    clock = FakeClock(when)
    run_daily(app.state.session_factory, FakeSource(outcomes), trigger="manual", clock=clock, sleep=clock.sleep)


def two_days(app):
    sf = app.state.session_factory
    py = add_listing(sf, "Python BG", keywords="python")
    add_listing(sf, "Data Eng Berlin", keywords="data")
    seed(app, datetime(2026, 10, 26, 9, 0), {"python": [[p("1", title="Old Job")]], "data": [[]]})
    seed(app, datetime(2026, 10, 28, 9, 0), {"python": [[p("2", title="Django Dev"), p("3", title="Flask Dev")]],
                                              "data": [SourceBlocked("rate limited by LinkedIn")]})
    return py


def test_empty_state(app_factory):
    with TestClient(app_factory()) as c:
        assert "No reports yet" in c.get("/").text


def test_report_split_view_and_tabs(app_factory):
    app = app_factory()
    two_days(app)
    with TestClient(app) as c:
        home = c.get("/").text
    assert 'id="job-list"' in home and 'role="listbox"' in home and 'id="job-pane"' in home
    assert "Django Dev" in home and "Old Job" not in home
    for tab in ("All", "Python BG", "Data Eng Berlin"):
        assert f">{tab}<" in home
    assert "rate limited by LinkedIn" in home and 'href="/report/2026-10-26"' in home
    assert 'class="job-item unseen"' in home


def test_listing_tab_and_unseen_filter(app_factory):
    app = app_factory()
    two_days(app)
    flask = job_id(app.state.session_factory, "3")
    with TestClient(app) as c:
        assert "Django Dev" not in c.get("/report/2026-10-28", params={"listing": 999}).text
        c.get(f"/jobs/{flask}/pane")
        unseen = c.get("/report/2026-10-28", params={"unseen": "1"}).text
    assert "Django Dev" in unseen and "Flask Dev" not in unseen and "1 unseen" in unseen


def test_preselected_job(app_factory):
    app = app_factory()
    two_days(app)
    jid = job_id(app.state.session_factory, "2")
    with TestClient(app) as c:
        page = c.get("/report/2026-10-28", params={"job": jid}).text
    assert f'id="job-item-{jid}" class="job-item' in page and 'aria-selected="true"' in page
    assert f'id="pane-title-{jid}"' in page and "has-selection" in page


def test_htmx_list_fragment(app_factory):
    app = app_factory()
    two_days(app)
    with TestClient(app) as c:
        fragment = c.get("/report/2026-10-28", headers={"HX-Request": "true"}).text
    assert "job-item-" in fragment and 'class="topbar"' not in fragment


def test_pane_marks_seen_and_404(app_factory):
    app = app_factory()
    two_days(app)
    jid = job_id(app.state.session_factory, "2")
    with TestClient(app) as c:
        pane = c.get(f"/jobs/{jid}/pane").text
        assert c.get("/jobs/9999/pane").status_code == 404
    assert 'data-action="save"' in pane and "found by Python BG" in pane and "<html" not in pane
    with app.state.session_factory() as s:
        assert s.get(Job, jid).seen_at is not None


def test_status_change_returns_pane_row_and_previous(app_factory):
    app = app_factory()
    add_listing(app.state.session_factory, "Python BG", keywords="python")
    seed(app, datetime(2026, 10, 28, 9, 0), {"python": [[p("2", title='Django "Dev" <b>')]]})
    jid = job_id(app.state.session_factory, "2")
    with TestClient(app) as c:
        r = c.post(f"/jobs/{jid}/status", data={"status": "hidden", "view": "pane"})
        assert c.post(f"/jobs/{jid}/status", data={"status": "bogus", "view": "pane"}).status_code == 400
        assert c.post("/jobs/9999/status", data={"status": "new", "view": "pane"}).status_code == 404
    trigger = json.loads(r.headers["HX-Trigger-After-Settle"])["jobStatusChanged"]
    assert trigger == {"jobId": jid, "status": "hidden", "previous": "new", "label": "Hidden"}
    assert f'id="job-item-{jid}"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
    assert 'data-action="reset"' in r.text and "Django &#34;Dev&#34; &lt;b&gt;" in r.text
