from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import update
from starlette.datastructures import QueryParams

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.actions import mark_seen
from jobhunt.filters import (JobFilters, company_names, filter_options, filtered_jobs, hidden_count,
                             ordered_ids, report_jobs, report_tabs, tab_counts, unseen_count)
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.sources.base import SourceBlocked

TZ, NOW = ZoneInfo("Europe/Belgrade"), datetime(2026, 10, 28, 9, 0)
DAY = date(2026, 10, 28)


def run_at(sf, src, when=NOW):
    clock = FakeClock(when)
    return run_daily(sf, src, trigger="manual", clock=clock, sleep=clock.sleep)


def seed(sf, postings, keywords="python", name="A"):
    listing = add_listing(sf, name, keywords=keywords)
    run_at(sf, FakeSource({keywords: [postings]}))
    return listing


def set_job(sf, external_id, **values):
    with sf() as s:
        s.exec(update(Job).where(Job.external_id == external_id).values(**values))
        s.commit()


def ids(sf, f=JobFilters(), ordered=False, **kw):
    with sf() as s:
        jobs, _ = filtered_jobs(s, f, NOW, **kw)
    found = [j.external_id for j in jobs]
    return found if ordered else sorted(found)


def test_tabs(session_factory):
    seed(session_factory, [p(str(i)) for i in range(1, 5)])
    for external_id, status in (("2", "interested"), ("3", "applied"), ("4", "hidden")):
        set_job(session_factory, external_id, status=status)
    expected = {"active": ["1", "2", "3"], "undecided": ["1"], "saved": ["2"], "applied": ["3"], "hidden": ["4"],
                "all": ["1", "2", "3", "4"]}
    for tab, want in expected.items():
        assert ids(session_factory, JobFilters(tab=tab)) == want
    with session_factory() as s:
        assert tab_counts(s, JobFilters(), NOW) == {tab: len(want) for tab, want in expected.items()}


def test_workplace_language_seniority_type_company(session_factory):
    seed(session_factory, [p("1", company="Acme"), p("2", company="Нордеус"), p("3", company="Beta"),
                           p("4", company="Gamma")])
    set_job(session_factory, "1", workplace="remote", language="de", seniority="Mid-Senior level",
            employment_type="Full-time")
    set_job(session_factory, "2", workplace="hybrid", language="en", seniority="Associate")
    set_job(session_factory, "3", workplace=None, language="it", employment_type="Contract")
    set_job(session_factory, "4", workplace=None, language=None)
    assert ids(session_factory, JobFilters(workplace=("remote", "unknown"))) == ["1", "3", "4"]
    assert ids(session_factory, JobFilters(language="non-en")) == ["1", "3"]
    assert ids(session_factory, JobFilters(language="de")) == ["1"]
    assert ids(session_factory, JobFilters(language="en")) == ["2"]
    assert ids(session_factory, JobFilters(seniority="Associate")) == ["2"]
    assert ids(session_factory, JobFilters(employment_type="Contract")) == ["3"]
    assert ids(session_factory, JobFilters(company="acme")) == ["1"]
    assert ids(session_factory, JobFilters(company="нордеус")) == ["2"]


def test_found_and_posted_within(session_factory):
    seed(session_factory, [p("1"), p("2"), p("3")])
    set_job(session_factory, "1", first_seen_at=NOW - timedelta(hours=2), posted_at=date(2026, 10, 28))
    set_job(session_factory, "2", first_seen_at=NOW - timedelta(days=2), posted_at=date(2026, 10, 26))
    set_job(session_factory, "3", first_seen_at=NOW - timedelta(days=10), posted_at=None)
    assert ids(session_factory, JobFilters(found_within="3d")) == ["1", "2"]
    assert ids(session_factory, JobFilters(posted_within="24h")) == ["1"]


def test_sort_orders(session_factory):
    seed(session_factory, [p("1", company="Zeta"), p("2", company="alpha"), p("3", company="Mid")])
    set_job(session_factory, "1", first_seen_at=NOW - timedelta(hours=3), posted_at=date(2026, 10, 20))
    set_job(session_factory, "2", first_seen_at=NOW - timedelta(hours=1), posted_at=None)
    set_job(session_factory, "3", first_seen_at=NOW - timedelta(hours=2), posted_at=date(2026, 10, 25))
    assert ids(session_factory, ordered=True) == ["2", "3", "1"]
    assert ids(session_factory, JobFilters(sort="posted"), ordered=True) == ["3", "1", "2"]
    assert ids(session_factory, JobFilters(sort="company"), ordered=True) == ["2", "3", "1"]


def test_listing_filter(session_factory):
    a = add_listing(session_factory, "A", keywords="python")
    b = add_listing(session_factory, "B", keywords="go")
    run_at(session_factory, FakeSource({"python": [[p("1"), p("2")]], "go": [[p("2"), p("3")]]}))
    assert ids(session_factory, JobFilters(listing_id=a)) == ["1", "2"]
    assert ids(session_factory, JobFilters(listing_id=b)) == ["2", "3"]


def test_pagination(session_factory):
    seed(session_factory, [p(str(i)) for i in range(51)])
    with session_factory() as s:
        jobs, total = filtered_jobs(s, JobFilters(), NOW, offset=50)
    assert (len(jobs), total) == (1, 51)


def test_filters_from_query_is_lenient():
    f = JobFilters.from_query(QueryParams("tab=bogus&sort=x&listing=²&workplace=remote&workplace=moon&found_within=9y&q=+php+"))
    assert f == JobFilters(workplace=("remote",), q="php")
    assert JobFilters().query() == "" and f.query(sort="posted") == "workplace=remote&q=php&sort=posted"
    assert [label for label, _ in f.chips()] == ["Remote", "“php”"]
    assert dict(f.chips())["Remote"] == "q=php"


def three_listings(sf):
    add_listing(sf, "Python BG", keywords="python")
    add_listing(sf, "Go BG", keywords="go")
    add_listing(sf, "Data Eng Berlin", keywords="data")
    return FakeSource({
        "python": [[p("1", posted_at=date(2026, 10, 26)), p("2", posted_at=date(2026, 10, 27)), p("3")]],
        "go": [[p("2", posted_at=date(2026, 10, 27))]],
        "data": [SourceBlocked("rate limited by LinkedIn")],
    })


def test_report_tabs_and_jobs(session_factory):
    run_at(session_factory, three_listings(session_factory))
    with session_factory() as s:
        tabs = [(t.listing.name if t.listing else "All", t.count, t.error) for t in report_tabs(s, DAY, TZ)]
        everything = [j.external_id for j in report_jobs(s, DAY, TZ, JobFilters(), NOW)]
    assert tabs == [("All", 3, None), ("Python BG", 3, None), ("Go BG", 0, None),
                    ("Data Eng Berlin", 0, "rate limited by LinkedIn")]
    assert everything == ["2", "1", "3"]
    mark_seen(session_factory, job_id(session_factory, "2"), NOW)
    with session_factory() as s:
        assert [j.external_id for j in report_jobs(s, DAY, TZ, JobFilters(), NOW, unseen_only=True)] == ["1", "3"]
        assert unseen_count(s, DAY, TZ) == 2
        assert report_tabs(s, date(2026, 10, 27), TZ) == []


def test_filter_options_and_company_names(session_factory):
    seed(session_factory, [p("1", company="Acme"), p("2", company="Acme Labs"), p("3", company="Beta")])
    set_job(session_factory, "1", language="it", seniority="Associate", employment_type="Full-time")
    set_job(session_factory, "2", language="de", seniority="Associate")
    with session_factory() as s:
        options = filter_options(s)
        assert (options.languages, options.seniorities, options.employment_types) == (
            ["de", "it"], ["Associate"], ["Full-time"])
        assert company_names(s, "acme") == ["Acme", "Acme Labs"]


def test_cursor_paging_does_not_skip_jobs_after_decisions(session_factory):
    seed(session_factory, [p(str(i)) for i in range(60)])
    f = JobFilters(tab="undecided")
    with session_factory() as s:
        first, total = filtered_jobs(s, f, NOW, limit=50)
    assert (len(first), total) == (50, 60)
    for job in first[:10]:
        set_job(session_factory, job.external_id, status="interested")
    with session_factory() as s:
        rest, _ = filtered_jobs(s, f, NOW, limit=50, cursor=first[-1].id)
    shown = {j.external_id for j in first} | {j.external_id for j in rest}
    assert len(rest) == 10 and len(shown) == 60


def test_cursor_paging_for_each_sort(session_factory):
    seed(session_factory, [p(str(i), company=f"Co {i % 7}", posted_at=None if i % 5 == 0 else date(2026, 10, 1 + i % 20))
                           for i in range(23)])
    for sort in ("found", "posted", "company"):
        f, seen, cursor = JobFilters(sort=sort), [], None
        with session_factory() as s:
            everything = [j.external_id for j in filtered_jobs(s, f, NOW, limit=100)[0]]
            while True:
                page, _ = filtered_jobs(s, f, NOW, limit=5, cursor=cursor)
                if not page:
                    break
                seen += [j.external_id for j in page]
                cursor = page[-1].id
        assert seen == everything, sort


def test_not_eligible_new_jobs_hidden_by_default(session_factory):
    sf = session_factory
    seed(sf, [p("1"), p("2"), p("3"), p("4")])
    set_job(sf, "1", eligibility="not_eligible")
    set_job(sf, "2", eligibility="not_eligible", status="applied")
    set_job(sf, "3", eligibility="eligible", eligibility_override="not_eligible")
    set_job(sf, "4", eligibility="not_eligible", eligibility_override="eligible")
    assert ids(sf) == ["2", "4"]
    assert ids(sf, JobFilters(eligibility=("not_eligible",))) == ["1", "2", "3"]
    assert ids(sf, JobFilters(eligibility=("eligible",))) == ["4"]
    with sf() as s:
        assert hidden_count(s, JobFilters(), NOW) == 2
        assert hidden_count(s, JobFilters(eligibility=("eligible",)), NOW) == 0
        assert tab_counts(s, JobFilters(), NOW)["undecided"] == 1
        assert [i for i, _ in ordered_ids(s, JobFilters(tab="undecided"), NOW)] == [job_id(sf, "4")]
    f = JobFilters.from_query(QueryParams("eligibility=relocate&eligibility=bogus"))
    assert f.eligibility == ("relocate",) and f.query() == "eligibility=relocate" and f.chips()[0][0] == "Relocate"
