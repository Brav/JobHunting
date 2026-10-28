import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from fakes import FakeClock, FakeSource, FakeTranslator, add_listing, add_run, make_posting as p, wait_until
from jobhunt.config import load_settings
from jobhunt.models import Run
from jobhunt.runner import record_results
from jobhunt.sources.base import JobPage, SourceBlocked
from jobhunt.translate import TranslationQuotaExceeded
from jobhunt.scheduler import RunCoordinator, latest_run_started, latest_slot, run_hours, should_catch_up

TZ, TEN = ZoneInfo("Europe/Belgrade"), time(10, 0)


@pytest.fixture
def settings(tmp_path):
    return load_settings({"JOBHUNT_DB_PATH": str(tmp_path / "unused.db")})


def test_run_hours():
    assert run_hours(time(10, 0), 4) == [2, 6, 10, 14, 18, 22]
    assert run_hours(time(10, 0), 24) == [10]
    assert run_hours(time(7, 30), 6) == [1, 7, 13, 19]


def test_latest_slot():
    assert latest_slot(datetime(2026, 10, 28, 9, 30), TZ, TEN, 4) == datetime(2026, 10, 28, 9, 0)    # 10:30 → 10:00
    assert latest_slot(datetime(2026, 10, 28, 8, 59), TZ, TEN, 4) == datetime(2026, 10, 28, 5, 0)    # 09:59 → 06:00
    assert latest_slot(datetime(2026, 10, 27, 23, 30), TZ, TEN, 4) == datetime(2026, 10, 27, 21, 0)  # 00:30 → 22:00 the day before
    assert latest_slot(datetime(2026, 7, 1, 8, 30), TZ, TEN, 4) == datetime(2026, 7, 1, 8, 0)        # 10:30 CEST → 10:00 CEST


def test_should_catch_up():
    now = datetime(2026, 10, 28, 9, 30)  # 10:30 local; latest slot 10:00 local = 09:00 UTC
    assert should_catch_up(now, TZ, TEN, 4, None)
    assert should_catch_up(now, TZ, TEN, 4, datetime(2026, 10, 28, 8, 0))       # last run 09:00 local
    assert not should_catch_up(now, TZ, TEN, 4, datetime(2026, 10, 28, 9, 0))
    assert not should_catch_up(now, TZ, TEN, 4, datetime(2026, 10, 28, 9, 10))


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
        assert nxt.hour in (2, 6, 10, 14, 18, 22) and nxt.minute == 0
    finally:
        coord.shutdown()


def test_catch_up_check_runs_only_after_a_missed_slot(session_factory, settings):
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep)
    assert coord.catch_up_if_needed() is True
    wait_until(lambda: not coord.is_running)
    assert coord.catch_up_if_needed() is False
    clock.now = datetime(2026, 10, 28, 13, 5)  # 14:05 local: the 14:00 slot was missed
    assert coord.catch_up_if_needed() is True
    wait_until(lambda: not coord.is_running)
    with session_factory() as s:
        assert [r.trigger for r in s.exec(select(Run)).all()] == ["catch_up", "catch_up"]


def seed_jobs(sf, n):
    listing, run = add_listing(sf, "A"), add_run(sf, datetime(2026, 10, 28, 9, 0))
    with sf() as s:
        record_results(s, run, listing, [p(str(i)) for i in range(1, n + 1)], datetime(2026, 10, 28, 9, 0))


def test_enrich_backs_off_after_a_block(session_factory, settings):
    seed_jobs(session_factory, 2)

    def blocked(eid):
        raise SourceBlocked("rate limited by LinkedIn")

    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep,
                           fetch_page=blocked, translator=FakeTranslator())
    assert coord.enrich_if_idle().blocked
    assert coord.enrich_if_idle() is None
    clock.now += timedelta(minutes=31)
    assert coord.enrich_if_idle() is not None


def test_enrich_waits_for_a_running_search(session_factory, settings):
    seed_jobs(session_factory, 1)
    started, gate = threading.Event(), threading.Event()

    class SlowSource:
        def search(self, **kw):
            started.set()
            gate.wait(5)
            return []

    coord = RunCoordinator(session_factory, SlowSource(), settings, sleep=lambda s: None,
                           fetch_page=lambda eid: JobPage("We are hiring PHP developers in Berlin.", []),
                           translator=FakeTranslator())
    coord.trigger("manual")
    assert started.wait(5)
    assert coord.enrich_if_idle() is None
    gate.set()
    wait_until(lambda: not coord.is_running)
    assert coord.enrich_if_idle().downloaded == 1


def test_enrich_pauses_titles_after_quota(session_factory, settings):
    seed_jobs(session_factory, 1)
    translator = FakeTranslator(fail=TranslationQuotaExceeded("quota"))
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep,
                           fetch_page=lambda eid: JobPage("Wir suchen einen erfahrenen PHP-Entwickler in Berlin.", []),
                           translator=translator)
    assert coord.enrich_if_idle().quota_exceeded
    coord.enrich_if_idle()
    assert len(translator.calls) == 1
    clock.now = datetime(2026, 11, 1, 0, 5)
    coord.enrich_if_idle()
    assert len(translator.calls) == 2
