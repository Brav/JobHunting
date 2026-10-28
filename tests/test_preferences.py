import json
from dataclasses import replace
from datetime import datetime

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt import checks
from jobhunt.models import Setting
from jobhunt.preferences import Preferences, load_preferences, save_preferences
from jobhunt.runner import run_daily

NOW = datetime(2026, 10, 28, 9, 0)


def test_preferences_default_and_round_trip(session_factory):
    with session_factory() as s:
        assert load_preferences(s) == Preferences()
        save_preferences(s, replace(Preferences(), follow_up_days=10, skills=("PHP", "Laravel")), NOW)
    with session_factory() as s:
        prefs = load_preferences(s)
        assert (prefs.follow_up_days, prefs.skills) == (10, ("PHP", "Laravel"))


def test_preferences_tolerate_missing_and_unknown_keys(session_factory):
    with session_factory() as s:
        s.add(Setting(key="preferences", value=json.dumps({"follow_up_days": 3, "gone": 1})))
        s.commit()
        prefs = load_preferences(s)
        assert (prefs.follow_up_days, prefs.auto_close_days) == (3, 30)


def test_checks_refresh_all(session_factory):
    add_listing(session_factory, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(session_factory, FakeSource({"python": [[p("1"), p("2")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    with session_factory() as s:
        assert checks.refresh_all(s) == 2
        assert checks.refresh_all(s, stale_only=True) == 0


def test_preferences_form(app_factory):
    from fastapi.testclient import TestClient
    app = app_factory()
    with TestClient(app) as c:
        form = {"skills": "PHP, Laravel ,, Vue", "follow_up_days": "10", "auto_close_days": "45",
                "goal_applications_per_week": "6", "goal_reviews_per_week": "40", "paused_until": "",
                "sources": ["remoteok", "hn"], "ntfy_url": "https://ntfy.sh/my-jobs"}
        r = c.post("/settings/preferences", data=form, follow_redirects=False)
        assert r.status_code == 303
        with app.state.session_factory() as s:
            prefs = load_preferences(s)
        assert prefs.skills == ("PHP", "Laravel", "Vue") and prefs.follow_up_days == 10
        assert prefs.sources == ("remoteok", "hn") and prefs.ntfy_url == "https://ntfy.sh/my-jobs"
        page = c.get("/settings").text
        assert 'value="PHP, Laravel, Vue"' in page and 'name="sources" value="himalayas"' in page
        for change, message in [({"follow_up_days": "0"}, "Follow-up days must be between 1 and 90"),
                                ({"paused_until": "soon"}, "Enter the pause date as a date"),
                                ({"ntfy_url": "ftp://x"}, "The push address must start with https://")]:
            r = c.post("/settings/preferences", data={**form, **change})
            assert r.status_code == 422 and message in r.text
