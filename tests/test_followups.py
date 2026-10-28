from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlmodel import select, update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.actions import set_job_status
from jobhunt.applications import add_step, delete_step, steps_for
from jobhunt.followups import auto_close, follow_up_due
from jobhunt.models import ApplicationStep, Job
from jobhunt.preferences import Preferences
from jobhunt.runner import run_daily
from jobhunt.stats import application_row, load_application
from jobhunt.timeutil import local_date, utcnow

TZ, NOW = ZoneInfo("Europe/Belgrade"), datetime(2026, 10, 28, 9, 0)
TODAY = NOW.date()


def applied(sf, *ids, days_ago=0):
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(sf, FakeSource({"python": [[p(i) for i in ids]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    jids = [job_id(sf, i) for i in ids]
    with sf() as s:
        for j in jids:
            set_job_status(s, j, "applied", NOW, TZ)
            s.exec(update(ApplicationStep).where(ApplicationStep.job_id == j)
                   .values(on_date=TODAY - timedelta(days=days_ago)))
        s.commit()
    return jids


def due(sf, j, today=TODAY):
    with sf() as s:
        return follow_up_due(s.get(Job, j), load_application(s, j), Preferences(), today)


def test_follow_up_due(session_factory):
    sf = session_factory
    a, b, c = applied(sf, "1", "2", "3", days_ago=8)
    assert due(sf, a)
    with sf() as s:
        s.exec(update(Job).where(Job.id == b).values(followed_up_on=TODAY))
        add_step(s, c, "hr_interview", TODAY - timedelta(days=8), "", TODAY, NOW)
        s.commit()
    assert not due(sf, b) and not due(sf, c)
    assert due(sf, b, TODAY + timedelta(days=7))


def test_auto_close_once_and_only_at_applied(session_factory):
    sf = session_factory
    a, b = applied(sf, "1", "2", days_ago=31)
    with sf() as s:
        add_step(s, b, "hr_interview", TODAY - timedelta(days=31), "", TODAY, NOW)
        assert auto_close(s, Preferences(), TODAY, NOW) == 1
        assert auto_close(s, Preferences(), TODAY, NOW) == 0
        closed = steps_for(s, a)[0]
        assert (closed.stage, closed.note) == ("no_response", "Closed automatically after 30 days without a reply")
        assert s.get(Job, a).stage == "no_response" and s.get(Job, b).stage == "hr_interview"
        delete_step(s, a, closed.id)  # the user undoes it
        assert auto_close(s, Preferences(), TODAY + timedelta(days=1), NOW) == 0
        assert auto_close(s, Preferences(), TODAY + timedelta(days=30), NOW) == 1


def test_follow_up_route_and_stats(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
        j = job_id(sf, "1")
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        today = local_date(utcnow(), app.state.settings.tz)
        with sf() as s:
            s.exec(update(ApplicationStep).values(on_date=today - timedelta(days=9)))
            s.commit()
        html = c.get("/stats").text
        assert "1 follow-up due" in html and "Follow up" in html
        r = c.post(f"/jobs/{j}/followed-up", data={"view": "stats-row"})
        assert r.status_code == 200 and r.text.lstrip().startswith(f'<tr id="app-row-{j}"')
        with sf() as s:
            assert s.get(Job, j).followed_up_on == today
        assert "follow-up due" not in c.get("/stats").text
