from datetime import datetime, timedelta

from sqlmodel import select

from fakes import add_listing, add_run, make_posting as p
from jobhunt.models import Job, Match, RunListing
from jobhunt.runner import ListingResult, last_success_at, record_results, window_hours

NOW = datetime(2026, 10, 28, 9, 0)  # 10:00 in Belgrade


def test_window_hours():
    assert window_hours(None, NOW) == 168
    assert window_hours(NOW - timedelta(hours=24), NOW) == 26
    assert window_hours(NOW - timedelta(hours=5), NOW) == 24
    assert window_hours(NOW - timedelta(hours=30, minutes=10), NOW) == 33
    assert window_hours(NOW - timedelta(days=10), NOW) == 168


def test_last_success_ignores_failed_rows(session_factory):
    a, b = add_listing(session_factory, "A"), add_listing(session_factory, "B")
    r1, r2 = add_run(session_factory, NOW - timedelta(hours=48)), add_run(session_factory, NOW - timedelta(hours=24))
    with session_factory() as s:
        s.add(RunListing(run_id=r1, listing_id=a, attempts=1))
        s.add(RunListing(run_id=r2, listing_id=a, attempts=3, error="rate limited by LinkedIn"))
        s.commit()
        assert last_success_at(s, a) == NOW - timedelta(hours=48)
        assert last_success_at(s, b) is None


def test_new_then_repeat(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        assert record_results(s, r1, a, [p("1"), p("2"), p("2")], NOW) == ListingResult(found=2, new=2)
        assert record_results(s, r2, a, [p("2"), p("3")], NOW + timedelta(days=1)) == ListingResult(found=2, new=1)
        new_in_r2 = {s.get(Job, m.job_id).external_id: m.is_new
                     for m in s.exec(select(Match).where(Match.run_id == r2))}
    assert new_in_r2 == {"2": False, "3": True}


def test_newness_is_per_listing(session_factory):
    a, b = add_listing(session_factory, "A"), add_listing(session_factory, "B", keywords="go")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1")], NOW)
        assert record_results(s, r2, b, [p("1")], NOW + timedelta(days=1)) == ListingResult(found=1, new=1)
        assert len(s.exec(select(Job)).all()) == 1


def test_refresh_keeps_status_and_first_seen(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1")], NOW)
        job = s.exec(select(Job)).one()
        job.status = "applied"
        s.add(job)
        s.commit()
        record_results(s, r2, a, [p("1", title="Senior Engineer")], NOW + timedelta(days=1))
        job = s.exec(select(Job)).one()
    assert (job.title, job.status, job.first_seen_at) == ("Senior Engineer", "applied", NOW)
