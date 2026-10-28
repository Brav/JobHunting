from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily


def seed(app):
    add_listing(app.state.session_factory, "PHP", keywords="php")
    postings = [p(str(n), title=f"Job {n}", posted_at=date(2026, 10, 28 - n)) for n in range(1, 5)]
    clock = FakeClock(datetime(2026, 10, 28, 9, 0))
    run_daily(app.state.session_factory, FakeSource({"php": [postings]}), trigger="manual", clock=clock, sleep=clock.sleep)
    return {n: job_id(app.state.session_factory, str(n)) for n in range(1, 5)}


def set_job(app, jid, **values):
    with app.state.session_factory() as s:
        s.exec(update(Job).where(Job.id == jid).values(**values))
        s.commit()


def act(c, page_html, action):
    """Submit the focus form for `action` the way the page would."""
    import re
    form = re.search(rf'<form[^>]*data-focus-action="{action}".*?</form>', page_html, re.S).group(0)
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
    return c.post("/focus/act", data={k: v.replace("&amp;", "&") for k, v in fields.items()}, follow_redirects=False)


def test_focus_walks_undecided_jobs_and_summarises(app_factory):
    app = app_factory()
    ids = seed(app)
    set_job(app, ids[2], status="interested")
    with TestClient(app) as c:
        page = c.get("/focus", params={"day": "2026-10-28"}).text
        assert "Job 1" in page and "1 of 4" in page and 'x-data="focusPage"' in page
        r = act(c, page, "hide")
        assert r.status_code == 303 and f"after={ids[1]}" in r.headers["location"] and "n_hidden=1" in r.headers["location"]
        page = c.get(r.headers["location"]).text
        assert "Job 3" in page and "3 of 4" in page                      # job 2 is already saved
        page = c.get(act(c, page, "later").headers["location"]).text
        assert "Job 4" in page
        summary = c.get(act(c, page, "save").headers["location"]).text
    assert "Saved 1 · Applied 0 · Hidden 1 · Later 1" in summary and 'href="/report/2026-10-28"' in summary
    with app.state.session_factory() as s:
        later = s.get(Job, ids[3])
        assert (later.status, s.get(Job, ids[1]).status, s.get(Job, ids[4]).status) == ("new", "hidden", "interested")
        assert later.seen_at is not None


def test_focus_from_all_jobs_filters(app_factory):
    app = app_factory()
    ids = seed(app)
    set_job(app, ids[3], workplace="remote")
    with TestClient(app) as c:
        page = c.get("/focus", params={"source": "jobs", "workplace": "remote"}).text
        assert "Job 3" in page and "Job 1" not in page
        done = c.get(act(c, page, "applied").headers["location"]).text
    assert "Applied 1" in done and 'href="/jobs?workplace=remote"' in done


def test_focus_after_unknown_job(app_factory):
    app = app_factory()
    seed(app)
    with TestClient(app) as c:
        page = c.get("/focus", params={"day": "2026-10-28", "after": "99999", "n_saved": "x"}).text
    assert "Job 1" in page and "1 of 4" in page


def test_focus_act_validation(app_factory):
    app = app_factory()
    ids = seed(app)
    with TestClient(app) as c:
        assert c.post("/focus/act", data={"job_id": ids[1], "action": "delete", "back": ""}).status_code == 400
        assert c.post("/focus/act", data={"job_id": 99999, "action": "save", "back": ""}).status_code == 404
