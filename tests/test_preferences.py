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
