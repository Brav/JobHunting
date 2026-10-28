import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, add_run, wait_until
from jobhunt.config import load_settings
from jobhunt.models import Run
from jobhunt.scheduler import RunCoordinator, latest_run_started, should_catch_up

TZ, TEN = ZoneInfo("Europe/Belgrade"), time(10, 0)


@pytest.fixture
def settings(tmp_path):
    return load_settings({"JOBHUNT_DB_PATH": str(tmp_path / "unused.db")})


def test_should_catch_up_winter():
    assert not should_catch_up(datetime(2026, 10, 28, 8, 59), TZ, TEN, None)
    assert should_catch_up(datetime(2026, 10, 28, 9, 30), TZ, TEN, None)
    assert should_catch_up(datetime(2026, 10, 28, 9, 30), TZ, TEN, datetime(2026, 10, 28, 8, 0))  # manual run at 09:00
    assert not should_catch_up(datetime(2026, 10, 28, 9, 30), TZ, TEN, datetime(2026, 10, 28, 9, 0))
    assert should_catch_up(datetime(2026, 10, 28, 9, 30), TZ, TEN, datetime(2026, 10, 27, 9, 0))


def test_should_catch_up_summer():
    assert should_catch_up(datetime(2026, 7, 1, 8, 30), TZ, TEN, None)      # 10:30 CEST
    assert not should_catch_up(datetime(2026, 7, 1, 7, 59), TZ, TEN, None)  # 09:59 CEST


def test_latest_run_started_ignores_interrupted(session_factory):
    now = datetime(2026, 10, 28, 9, 5)
    add_run(session_factory, now - timedelta(days=1))
    add_run(session_factory, now, status="failed", error="interrupted")
    with session_factory() as s:
        assert latest_run_started(s) == now - timedelta(days=1)


def test_second_trigger_refused_while_running(session_factory, settings):
    add_listing(session_factory, "A")
    started, gate = threading.Event(), threading.Event()

    class SlowSource:
        def search(self, **kw):
            started.set()
            gate.wait(5)
            return []

    coord = RunCoordinator(session_factory, SlowSource(), settings, sleep=lambda s: None)
    assert coord.trigger("manual") is True
    assert started.wait(5)
    assert coord.is_running
    assert coord.trigger("manual") is False
    assert coord.run_blocking("manual") is None
    gate.set()
    wait_until(lambda: not coord.is_running)
    with session_factory() as s:
        runs = s.exec(select(Run)).all()
    assert [(r.trigger, r.status) for r in runs] == [("manual", "ok")]


def test_start_catches_up_and_schedules(session_factory, settings):
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep)
    coord.start()
    try:
        wait_until(lambda: not coord.is_running)
        with session_factory() as s:
            assert [r.trigger for r in s.exec(select(Run)).all()] == ["catch_up"]
        nxt = coord.next_run_at()
        assert (nxt.hour, nxt.minute) == (10, 0)
    finally:
        coord.shutdown()
