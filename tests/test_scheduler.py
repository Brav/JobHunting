import threading
from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from fakes import FakeClock, FakeSource, FakeTranslator, add_listing, add_run, make_posting as p, wait_until
from jobhunt.config import load_settings
from jobhunt.models import Listing, Run, RunListing
from jobhunt.runner import record_results
from jobhunt.sources.base import JobPage, SourceBlocked
from jobhunt.translate import TranslationQuotaExceeded
from jobhunt.preferences import Preferences, load_preferences, save_preferences
from jobhunt.schedule import times_of_day
from jobhunt.scheduler import RunCoordinator, default_schedule, due_listings

TZ, TEN = ZoneInfo("Europe/Belgrade"), time(10, 0)


@pytest.fixture
def settings(tmp_path):
    return load_settings({"JOBHUNT_DB_PATH": str(tmp_path / "unused.db")})


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


def listing(sf, name, schedule=None, created=datetime(2026, 10, 27), active=True):
    """A listing made before the test's clock (add_listing stamps the real time)."""
    listing_id = add_listing(sf, name, keywords=name.lower(), active=active)
    with sf() as s:
        row = s.get(Listing, listing_id)
        row.created_at, row.schedule = created, schedule
        s.add(row)
        s.commit()
    return listing_id


def attempt(sf, listing_id, started_at, error=None):
    run_id = add_run(sf, started_at, status="failed" if error else "ok", error=error)
    with sf() as s:
        s.add(RunListing(run_id=run_id, listing_id=listing_id))
        s.commit()


def test_start_runs_what_is_due_and_says_when_next(session_factory, settings):
    listing(session_factory, "A")
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep)
    coord.start()
    try:
        wait_until(lambda: not coord.is_running)
        with session_factory() as s:
            assert [r.trigger for r in s.exec(select(Run)).all()] == ["scheduled"]
        assert coord._scheduler.get_job("due_check") is not None and coord._scheduler.get_job("scheduled") is None
        nxt = coord.next_run_at()
        assert nxt.hour in (2, 6, 10, 14, 18, 22) and nxt.minute == 0
    finally:
        coord.shutdown()


def test_check_due_after_a_missed_slot(session_factory, settings):
    listing(session_factory, "A")
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep)
    assert coord.check_due() is True
    wait_until(lambda: not coord.is_running)
    assert coord.check_due() is False
    clock.now = datetime(2026, 10, 28, 13, 5)  # the 10:00 slot (UTC here) came and went
    assert coord.check_due() is True
    wait_until(lambda: not coord.is_running)
    with session_factory() as s:
        assert [r.trigger for r in s.exec(select(Run)).all()] == ["scheduled", "scheduled"]


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


CRON_JOBS = ("backup", "recheck", "digest_push", "auto_close", "closed")


def test_set_time_zone_moves_every_cron_job(session_factory, settings):
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep,
                           fetch_page=lambda external_id: JobPage("Job description.", []))
    coord.start()
    try:
        new_york = ZoneInfo("America/New_York")
        coord.set_time_zone(new_york)
        for job_id in CRON_JOBS:
            assert coord._scheduler.get_job(job_id).trigger.timezone == new_york, job_id
        assert coord.settings.tz == new_york
    finally:
        coord.shutdown()


def test_set_time_zone_without_a_running_scheduler(session_factory, settings):
    coord = RunCoordinator(session_factory, FakeSource({}), settings)
    coord.set_time_zone(ZoneInfo("Asia/Tokyo"))
    assert coord.settings.tz.key == "Asia/Tokyo"


def test_no_automatic_run_without_listings(session_factory, settings):
    """A new install has no searches yet: no empty "0 of 0" run, so the report keeps its "add a listing" hint."""
    clock = FakeClock(datetime(2026, 10, 28, 9, 30))
    coord = RunCoordinator(session_factory, FakeSource({}), settings, clock=clock, sleep=clock.sleep)
    assert coord.trigger("scheduled") is False and coord.check_due() is False
    with session_factory() as s:
        assert s.exec(select(Run)).all() == []



# --- the due rule (Europe/Belgrade, UTC+1 in late October; the default is every 4 h from 10:00) ---

NOW = datetime(2026, 10, 28, 9, 30)  # 10:30 local: the latest slot is 10:00 local = 09:00 UTC


def due(sf, settings, now=NOW, tz=TZ, prefs=None):
    with sf() as s:
        prefs = prefs or load_preferences(s)
        changed = datetime.fromisoformat(prefs.schedule_changed_at) if prefs.schedule_changed_at else None
        return due_listings(s, now, tz, default_schedule(prefs, settings), changed)


def test_owner_default_keeps_the_six_times(settings):
    assert times_of_day(default_schedule(Preferences(), settings)) == [time(h) for h in (2, 6, 10, 14, 18, 22)]


def test_due_after_a_missed_slot_and_not_after_searching(session_factory, settings):
    a = listing(session_factory, "A")
    assert due(session_factory, settings) == [a]
    attempt(session_factory, a, datetime(2026, 10, 28, 9, 1))
    assert due(session_factory, settings) == []
    attempt(session_factory, a, datetime(2026, 10, 28, 8, 0), error="rate limited")  # an older failure changes nothing
    assert due(session_factory, settings) == []


def test_new_listing_waits_for_its_next_time(session_factory, settings):
    listing(session_factory, "A", created=datetime(2026, 10, 28, 9, 20))
    assert due(session_factory, settings) == []
    assert due(session_factory, settings, now=datetime(2026, 10, 28, 13, 0)) != []  # 14:00 local


def test_edited_schedule_waits_for_its_next_time(session_factory, settings):
    a = listing(session_factory, "A", schedule="times:09:00,10:00")
    with session_factory() as s:
        row = s.get(Listing, a)
        row.schedule_changed_at = datetime(2026, 10, 28, 9, 10)
        s.add(row)
        s.commit()
    assert due(session_factory, settings) == []


def test_edited_default_waits_for_its_next_time(session_factory, settings):
    on_default = listing(session_factory, "A")
    own = listing(session_factory, "B", schedule="times:10:00")
    prefs = replace(Preferences(), schedule_changed_at="2026-10-28T09:10")
    assert due(session_factory, settings, prefs=prefs) == [own]
    assert on_default in due(session_factory, settings, prefs=prefs, now=datetime(2026, 10, 28, 13, 0))  # 14:00


def test_switched_off_listing_is_never_due(session_factory, settings):
    listing(session_factory, "A", active=False)
    assert due(session_factory, settings) == []


def test_bad_stored_schedule_follows_the_default(session_factory, settings):
    a = listing(session_factory, "A", schedule="garbage")
    assert due(session_factory, settings) == [a]


def test_interrupted_run_does_not_count(session_factory, settings):
    a = listing(session_factory, "A")
    attempt(session_factory, a, datetime(2026, 10, 28, 9, 1), error="interrupted")
    assert due(session_factory, settings) == [a]


def test_zone_change_gives_at_most_one_run(session_factory, settings):
    a = listing(session_factory, "A")
    attempt(session_factory, a, datetime(2026, 10, 28, 9, 1))  # searched at the 10:00 Belgrade slot
    new_york = ZoneInfo("America/New_York")
    later = datetime(2026, 10, 28, 14, 30)  # 10:30 in New York: its 10:00 slot is 14:00 UTC
    assert due(session_factory, settings, now=later, tz=new_york) == [a]
    attempt(session_factory, a, datetime(2026, 10, 28, 14, 31))
    assert due(session_factory, settings, now=later, tz=new_york) == []


def test_check_due_runs_only_due_listings_in_one_run(session_factory, settings):
    a = listing(session_factory, "A")
    b = listing(session_factory, "B", schedule="times:20:00")
    attempt(session_factory, b, datetime(2026, 10, 27, 19, 30))  # B searched at its 20:00 slot yesterday
    clock = FakeClock(NOW)
    coord = RunCoordinator(session_factory, FakeSource({}), replace(settings, tz=TZ), clock=clock, sleep=clock.sleep)
    assert coord.check_due() is True
    wait_until(lambda: not coord.is_running)
    with session_factory() as s:
        run = s.exec(select(Run).where(Run.trigger == "scheduled")).one()
        assert [r.listing_id for r in s.exec(select(RunListing).where(RunListing.run_id == run.id))] == [a]


def test_check_due_skips_while_paused(session_factory, settings):
    listing(session_factory, "A")
    with session_factory() as s:
        save_preferences(s, replace(load_preferences(s), paused_until="2099-01-01"), NOW)
    clock = FakeClock(NOW)
    coord = RunCoordinator(session_factory, FakeSource({}), replace(settings, tz=TZ), clock=clock, sleep=clock.sleep)
    assert coord.check_due() is False


def test_next_run_at_is_the_earliest_listing_time(session_factory, settings):
    listing(session_factory, "A", schedule="times:12:30")
    listing(session_factory, "B")  # default: next is 14:00 local
    clock = FakeClock(NOW)
    coord = RunCoordinator(session_factory, FakeSource({}), replace(settings, tz=TZ), clock=clock, sleep=clock.sleep)
    nxt = coord.next_run_at()
    assert (nxt.hour, nxt.minute, nxt.tzinfo) == (12, 30, TZ)


def test_no_next_run_without_active_listings(session_factory, settings):
    coord = RunCoordinator(session_factory, FakeSource({}), settings)
    assert coord.next_run_at() is None


def test_push_follows_the_default(session_factory, settings):
    clock = FakeClock(NOW)
    coord = RunCoordinator(session_factory, FakeSource({}), replace(settings, tz=TZ), clock=clock, sleep=clock.sleep)
    coord.start()
    try:
        assert (coord._scheduler.get_job("digest_push").next_run_time.hour,
                coord._scheduler.get_job("digest_push").next_run_time.minute) == (10, 30)
        with session_factory() as s:
            save_preferences(s, replace(load_preferences(s), schedule="times:07:00"), NOW)
        coord.schedule_changed()
        nxt = coord._scheduler.get_job("digest_push").next_run_time
        assert (nxt.hour, nxt.minute) == (7, 30)
    finally:
        coord.shutdown()
