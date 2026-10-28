from datetime import date, datetime, timedelta

from sqlmodel import select

from fakes import add_listing, add_run, job_id, make_posting as p
from jobhunt.models import Job, Match, RunListing
from jobhunt.runner import ListingResult, last_success_at, record_results, window_hours

NOW = datetime(2026, 10, 28, 9, 0)  # 10:00 in Belgrade


def test_window_hours():
    assert window_hours(None, NOW) == 168
    assert window_hours(NOW - timedelta(hours=24), NOW) == 26
    assert window_hours(NOW - timedelta(hours=5), NOW) == 12
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


def test_newness_is_global(session_factory):
    a, b = add_listing(session_factory, "A"), add_listing(session_factory, "B", keywords="go")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1")], NOW)
        assert record_results(s, r2, b, [p("1")], NOW + timedelta(days=1)) == ListingResult(found=1, new=0)
        assert len(s.exec(select(Job)).all()) == 1


def test_same_job_from_two_listings_in_one_run(session_factory):
    a, b = add_listing(session_factory, "A"), add_listing(session_factory, "B", keywords="go")
    r = add_run(session_factory, NOW)
    with session_factory() as s:
        assert record_results(s, r, a, [p("1")], NOW) == ListingResult(found=1, new=1)
        assert record_results(s, r, b, [p("1")], NOW) == ListingResult(found=1, new=0)
        assert {m.listing_id: m.is_new for m in s.exec(select(Match))} == {a: True, b: False}


def test_repost_with_new_id_is_same_job(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1", title="PHP Developer")], NOW)
        assert record_results(s, r2, a, [p("2", title="php developer!")], NOW + timedelta(days=1)) == ListingResult(1, 0)
        job = s.exec(select(Job)).one()
    assert (job.external_id, job.title) == ("1", "PHP Developer")


def test_duplicates_within_one_result_collapse(session_factory):
    a = add_listing(session_factory, "A")
    r = add_run(session_factory, NOW)
    with session_factory() as s:
        postings = [p("1", title="PHP Dev"), p("2", title="PHP Dev"), p("1", title="PHP Dev")]
        assert record_results(s, r, a, postings, NOW) == ListingResult(found=1, new=1)


def test_two_postings_resolving_to_one_job_record_one_match(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1", title="PHP Developer")], NOW)
        postings = [p("3", title="PHP Developer"), p("1", title="PHP Developer (Remote)")]
        assert record_results(s, r2, a, postings, NOW + timedelta(days=1)) == ListingResult(found=1, new=0)
        assert len(s.exec(select(Match).where(Match.run_id == r2)).all()) == 1


def test_id_match_wins_over_key_match(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1", title="PHP Developer"), p("2", title="Go Developer")], NOW)
        assert record_results(s, r2, a, [p("2", title="Go Lead")], NOW + timedelta(days=1)) == ListingResult(1, 0)
        assert s.get(Job, job_id(session_factory, "2")).title == "Go Lead"


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


def test_newer_repost_takes_over_link_and_date(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1", title="PHP Developer", posted_at=date(2026, 9, 1))], NOW)
        job = s.exec(select(Job)).one()
        job.description = "old posting text"
        job.details, job.description_en, job.language, job.title_en = "[]", "old", "de", "PHP Developer EN"
        s.add(job)
        s.commit()
        repost = p("7", title="PHP Developer", posted_at=date(2026, 10, 27))
        assert record_results(s, r2, a, [repost], NOW + timedelta(days=1)) == ListingResult(1, 0)
        job = s.exec(select(Job)).one()
    assert (job.external_id, job.url, job.posted_at, job.description) == (
        "7", "https://www.linkedin.com/jobs/view/7", date(2026, 10, 27), None)
    assert (job.details, job.description_en, job.language, job.title_en) == (None, None, "de", "PHP Developer EN")


def test_same_day_repost_keeps_first_link(session_factory):
    a = add_listing(session_factory, "A")
    r1, r2 = add_run(session_factory, NOW), add_run(session_factory, NOW + timedelta(days=1))
    with session_factory() as s:
        record_results(s, r1, a, [p("1", title="PHP Developer", posted_at=date(2026, 10, 27))], NOW)
        record_results(s, r2, a, [p("7", title="PHP Developer", posted_at=date(2026, 10, 27))], NOW + timedelta(days=1))
        assert s.exec(select(Job)).one().external_id == "1"
