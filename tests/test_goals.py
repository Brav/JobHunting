from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlmodel import update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.models import Job
from jobhunt.preferences import Preferences, is_paused, load_preferences, save_preferences
from jobhunt.runner import run_daily
from jobhunt.timeutil import local_date, utcnow


def test_is_paused():
    today = local_date(utcnow(), ZoneInfo("Europe/Belgrade"))
    assert not is_paused(Preferences(), today)
    assert is_paused(Preferences(paused_until=today.isoformat()), today)
    assert not is_paused(Preferences(paused_until=(today - timedelta(days=1)).isoformat()), today)


def test_weekly_goals_and_pause(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1"), p("2"), p("3")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
        with sf() as s:
            s.exec(update(Job).values(seen_at=utcnow()))
            s.commit()
        c.post(f"/jobs/{job_id(sf, '1')}/status", data={"status": "applied"})
        html = c.get("/stats").text
        assert "Applied this week" in html and "1 / 5" in html and "Reviewed this week" in html and "3 / 50" in html
        assert "Typical reply rates" in html
        today = local_date(utcnow(), app.state.settings.tz)
        with sf() as s:
            save_preferences(s, replace(load_preferences(s), paused_until=(today + timedelta(days=3)).isoformat()), utcnow())
        assert "Searches paused until" in c.get("/").text
        assert app.state.coordinator.trigger("scheduled") is False
        assert app.state.coordinator.trigger("catch_up") is False
