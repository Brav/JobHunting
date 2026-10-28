from datetime import datetime

import pytest
from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.actions import delete_listing, mark_seen, set_job_status
from jobhunt.models import Job, Listing, Match, RunListing
from jobhunt.runner import run_daily

NOW = datetime(2026, 10, 28, 9, 0)


def test_set_job_status(session_factory):
    add_listing(session_factory, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(session_factory, FakeSource({"python": [[p("1")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    with session_factory() as s:
        job = set_job_status(s, job_id(session_factory, "1"), "interested", NOW)
        assert (job.status, job.status_changed_at) == ("interested", NOW)
        with pytest.raises(ValueError):
            set_job_status(s, job.id, "bogus", NOW)
        with pytest.raises(LookupError):
            set_job_status(s, 9999, "applied", NOW)


def test_delete_listing_keeps_tracked_jobs(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    add_listing(session_factory, "B", keywords="go")
    clock = FakeClock(NOW)
    run_daily(session_factory, FakeSource({"python": [[p("1"), p("2"), p("3"), p("4")]], "go": [[p("3")]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    with session_factory() as s:
        set_job_status(s, job_id(session_factory, "1"), "applied", NOW)
        set_job_status(s, job_id(session_factory, "4"), "hidden", NOW)
        delete_listing(s, a)
        assert s.get(Listing, a) is None
        assert s.exec(select(Match).where(Match.listing_id == a)).all() == []
        assert s.exec(select(RunListing).where(RunListing.listing_id == a)).all() == []
        assert sorted(j.external_id for j in s.exec(select(Job))) == ["1", "3", "4"]


def test_mark_seen_once_and_status_marks_seen(session_factory):
    add_listing(session_factory, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(session_factory, FakeSource({"python": [[p("1"), p("2")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    one, two = job_id(session_factory, "1"), job_id(session_factory, "2")
    later = datetime(2026, 10, 28, 12, 0)
    mark_seen(session_factory, one, NOW)
    mark_seen(session_factory, one, later)
    with session_factory() as s:
        set_job_status(s, two, "interested", later)
        assert (s.get(Job, one).seen_at, s.get(Job, two).seen_at) == (NOW, later)
