import random
from datetime import datetime, timedelta

from sqlalchemy import event
from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.actions import delete_listing
from jobhunt.models import Job, Listing, Match, Run, RunListing
from jobhunt.runner import run_daily
from jobhunt.sources.base import SourceBlocked, SourceError

NOW = datetime(2026, 10, 28, 9, 0)


def go(sf, src, clock=None):
    clock = clock or FakeClock(NOW)
    return run_daily(sf, src, trigger="scheduled", clock=clock, sleep=clock.sleep, rng=random.Random(0)), clock


def rows(sf, run_id):
    with sf() as s:
        return {r.listing_id: (r.attempts, r.found, r.new, r.error)
                for r in s.exec(select(RunListing).where(RunListing.run_id == run_id))}


def status(sf, run_id):
    with sf() as s:
        return s.get(Run, run_id).status


def test_happy_path(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    b = add_listing(session_factory, "B", keywords="go")
    add_listing(session_factory, "C", keywords="rust", active=False)
    src = FakeSource({"python": [[p("1"), p("2")]], "go": [[p("2")]]})
    run_id, clock = go(session_factory, src)
    with session_factory() as s:
        run = s.get(Run, run_id)
    assert (run.status, run.trigger, run.started_at) == ("ok", "scheduled", NOW)
    assert run.finished_at >= NOW
    assert rows(session_factory, run_id) == {a: (1, 2, 2, None), b: (1, 1, 0, None)}
    assert [c["keywords"] for c in src.calls] == ["python", "go"]
    assert (src.calls[0]["window_hours"], src.calls[0]["limit"], src.calls[0]["radius_km"]) == (168, 100, 25)
    assert len(clock.sleeps) == 1 and 3 <= clock.sleeps[0] <= 8


def test_second_run_window(session_factory):
    add_listing(session_factory, "A", keywords="python")
    src = FakeSource({"python": [[p("1")]]})
    _, clock = go(session_factory, src)
    clock.now = NOW + timedelta(hours=24)
    go(session_factory, src, clock)
    assert src.calls[1]["window_hours"] == 26


def test_retry_then_success(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    add_listing(session_factory, "B", keywords="go")
    src = FakeSource({"python": [SourceBlocked("rate limited by LinkedIn"), [p("1")]], "go": [[p("2")]]})
    run_id, clock = go(session_factory, src)
    assert status(session_factory, run_id) == "ok"
    assert rows(session_factory, run_id)[a] == (2, 1, 1, None)
    assert 120 in clock.sleeps and 600 not in clock.sleeps
    python_calls = [c for c in src.calls if c["keywords"] == "python"]
    assert python_calls[0]["window_hours"] == python_calls[1]["window_hours"]


def test_three_failures_make_partial(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    b = add_listing(session_factory, "B", keywords="go")
    src = FakeSource({"python": [SourceBlocked("rate limited by LinkedIn")], "go": [[p("2")]]})
    run_id, clock = go(session_factory, src)
    assert status(session_factory, run_id) == "partial"
    assert rows(session_factory, run_id) == {a: (3, 0, 0, "rate limited by LinkedIn"), b: (1, 1, 1, None)}
    assert 120 in clock.sleeps and 600 in clock.sleeps


def test_all_failed(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    run_id, _ = go(session_factory, FakeSource({"python": [SourceError("network down")]}))
    assert status(session_factory, run_id) == "failed"
    assert rows(session_factory, run_id)[a][3] == "network down"


def test_no_active_listings_is_ok(session_factory):
    src = FakeSource({})
    run_id, _ = go(session_factory, src)
    assert status(session_factory, run_id) == "ok" and src.calls == []


def test_unexpected_exception_fails_run(session_factory):
    add_listing(session_factory, "A", keywords="python")
    run_id, _ = go(session_factory, FakeSource({"python": [RuntimeError("kaboom")]}))
    with session_factory() as s:
        run = s.get(Run, run_id)
    assert (run.status, run.error) == ("failed", "RuntimeError: kaboom")
    assert run.finished_at is not None


def test_window_restarts_after_search_change(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    src = FakeSource({"python": [[p("1")]]})
    _, clock = go(session_factory, src)
    clock.now = NOW + timedelta(hours=24)
    with session_factory() as s:
        listing = s.get(Listing, a)
        listing.search_changed_at = NOW + timedelta(hours=1)
        s.add(listing)
        s.commit()
    go(session_factory, src, clock)
    assert src.calls[1]["window_hours"] == 168


def test_listing_deleted_before_its_turn_is_skipped(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    b = add_listing(session_factory, "B", keywords="go")
    src = FakeSource({"python": [[p("1")]], "go": [[p("2")]]})
    search = src.search

    def deleting_search(**kw):
        if kw["keywords"] == "python":
            with session_factory() as s:
                delete_listing(s, b)
        return search(**kw)

    src.search = deleting_search
    run_id, _ = go(session_factory, src)
    assert status(session_factory, run_id) == "ok"
    assert rows(session_factory, run_id) == {a: (1, 1, 1, None)}
    assert [c["keywords"] for c in src.calls] == ["python"]


def test_listing_deleted_during_its_search_leaves_no_rows(session_factory):
    b = add_listing(session_factory, "B", keywords="go")
    src = FakeSource({"go": [[p("2")]]})
    search = src.search

    def search_then_delete(**kw):
        result = search(**kw)
        with session_factory() as s:
            delete_listing(s, b)
        return result

    src.search = search_then_delete
    run_id, _ = go(session_factory, src)
    assert status(session_factory, run_id) == "ok"
    assert rows(session_factory, run_id) == {}
    with session_factory() as s:
        assert s.exec(select(Match)).all() == [] and s.exec(select(Job)).all() == []


def test_matches_are_saved_only_with_the_listing_attempt(session_factory):
    add_listing(session_factory, "A", keywords="python")

    def factory():
        session = session_factory()

        @event.listens_for(session, "before_flush")
        def fail_attempt(s, _context, _instances):
            if any(isinstance(row, RunListing) for row in s.new | s.dirty):
                raise RuntimeError("disk I/O error")

        return session

    run_id, _ = go(factory, FakeSource({"python": [[p("1")]]}))
    with session_factory() as s:
        assert (s.get(Run, run_id).status, s.exec(select(Match)).all(), s.exec(select(Job)).all()) == ("failed", [], [])
