from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.actions import set_job_status
from jobhunt.reports import RunSummary, listing_rows, report_dates, report_for_date, run_summary
from jobhunt.filters import JobFilters, filtered_jobs
from jobhunt.runner import run_daily
from jobhunt.sources.base import SourceBlocked

TZ, NOW = ZoneInfo("Europe/Belgrade"), datetime(2026, 10, 28, 9, 0)


def run_at(sf, src, when):
    clock = FakeClock(when)
    return run_daily(sf, src, trigger="manual", clock=clock, sleep=clock.sleep)


def three_listings(sf):
    add_listing(sf, "Python BG", keywords="python")
    add_listing(sf, "Go BG", keywords="go")
    add_listing(sf, "Data Eng Berlin", keywords="data")
    return FakeSource({
        "python": [[p("1", posted_at=date(2026, 10, 26)), p("2", posted_at=date(2026, 10, 27)), p("3")]],
        "go": [[p("2", posted_at=date(2026, 10, 27))]],
        "data": [SourceBlocked("rate limited by LinkedIn")],
    })


def test_report_sections(session_factory):
    run_at(session_factory, three_listings(session_factory), NOW)
    with session_factory() as s:
        set_job_status(s, job_id(session_factory, "1"), "hidden", NOW)
        sections = report_for_date(s, date(2026, 10, 28), TZ)
    assert [(x.listing.name, [j.external_id for j in x.jobs], x.error) for x in sections] == [
        ("Python BG", ["2", "3"], None),
        ("Go BG", [], None),
        ("Data Eng Berlin", [], "rate limited by LinkedIn"),
    ]


def test_job_found_by_two_listings_appears_once(session_factory):
    run_at(session_factory, three_listings(session_factory), NOW)
    with session_factory() as s:
        sections = report_for_date(s, date(2026, 10, 28), TZ)
        go_listing = sections[1].listing.id
        tracked = [j.external_id for j in filtered_jobs(s, JobFilters(listing_id=go_listing), NOW)[0]]
    shown = [j.external_id for x in sections for j in x.jobs]
    assert shown.count("2") == 1 and sections[0].listing.name == "Python BG" and "2" in [j.external_id for j in sections[0].jobs]
    assert tracked == ["2"]


def test_only_new_matches_count(session_factory):
    add_listing(session_factory, "Python BG", keywords="python")
    src = FakeSource({"python": [[p("1")], [p("1"), p("4")]]})
    run_at(session_factory, src, NOW)
    run_at(session_factory, src, NOW + timedelta(days=1))
    with session_factory() as s:
        assert [j.external_id for j in report_for_date(s, date(2026, 10, 29), TZ)[0].jobs] == ["4"]


def test_local_midnight_boundary(session_factory):
    add_listing(session_factory, "Python BG", keywords="python")
    run_at(session_factory, FakeSource({"python": [[p("1")]]}), datetime(2026, 10, 27, 23, 30))
    with session_factory() as s:
        assert report_dates(s, TZ) == [date(2026, 10, 28)]
        assert [j.external_id for j in report_for_date(s, date(2026, 10, 28), TZ)[0].jobs] == ["1"]
        assert report_for_date(s, date(2026, 10, 27), TZ) == []


def test_run_summary_and_listing_rows(session_factory):
    with session_factory() as s:
        assert run_summary(s) == RunSummary(run=None, total=0, ok=0, failures=[])
    run_at(session_factory, three_listings(session_factory), NOW)
    add_listing(session_factory, "Later", keywords="later")
    with session_factory() as s:
        summary = run_summary(s)
        rows = listing_rows(s)
    assert (summary.run.status, summary.total, summary.ok) == ("partial", 3, 2)
    assert summary.failures == [("Data Eng Berlin", "rate limited by LinkedIn")]
    assert [(r.listing.name, r.last and (r.last.found, r.last.new)) for r in rows] == [
        ("Python BG", (3, 3)), ("Go BG", (1, 0)), ("Data Eng Berlin", (0, 0)), ("Later", None)]
