from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import update

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily


def seed(app, postings, keywords="python"):
    add_listing(app.state.session_factory, keywords.title(), keywords=keywords)
    clock = FakeClock(datetime(2026, 10, 28, 9, 0))
    run_daily(app.state.session_factory, FakeSource({keywords: [postings]}), trigger="manual", clock=clock, sleep=clock.sleep)


def set_job(app, external_id, **values):
    with app.state.session_factory() as s:
        s.exec(update(Job).where(Job.external_id == external_id).values(**values))
        s.commit()


def test_tabs_counts_and_default_active(app_factory):
    app = app_factory()
    seed(app, [p("1", title="Visible Job"), p("2", title="Hidden Job")])
    set_job(app, "2", status="hidden")
    with TestClient(app) as c:
        page = c.get("/jobs").text
        hidden = c.get("/jobs", params={"tab": "hidden"}).text
    assert "Visible Job" in page and "Hidden Job" not in page and "1 job<" in page
    assert '<span>Active</span> <span class="count">1</span>' in page
    assert '<span>Hidden</span> <span class="count">1</span>' in page
    assert "Hidden Job" in hidden and "Visible Job" not in hidden


def test_filter_chips_and_clear_all(app_factory):
    app = app_factory()
    seed(app, [p("1", title="PHP Dev"), p("2", title="PHP Lead")])
    set_job(app, "1", workplace="remote", language="de")
    with TestClient(app) as c:
        page = c.get("/jobs", params=[("workplace", "remote"), ("language", "de"), ("q", "php")]).text
    assert "PHP Dev" in page and "PHP Lead" not in page
    assert 'href="/jobs?language=de&amp;q=php"' in page            # the Remote chip removes only workplace
    assert 'href="/jobs?workplace=remote&amp;q=php"' in page       # the German chip
    assert "“php”" in page and ">Clear all</a>" in page and 'href="/jobs"' in page


def test_cyrillic_search_and_company_filter(app_factory):
    app = app_factory()
    seed(app, [p("1", title="Програмер Python", company="Нордеус"), p("2", title="Go Developer", company="Acme")])
    with TestClient(app) as c:
        searched = c.get("/jobs", params={"q": "програмер"}).text
        by_company = c.get("/jobs", params={"company": "нордеус"}).text
    assert "Програмер Python" in searched and "Go Developer" not in searched
    assert "Програмер Python" in by_company and "Go Developer" not in by_company


def test_sort_posted(app_factory):
    app = app_factory()
    seed(app, [p("1", title="Older Post", posted_at=date(2026, 10, 1)), p("2", title="Newer Post", posted_at=date(2026, 10, 20))])
    with TestClient(app) as c:
        page = c.get("/jobs", params={"sort": "posted"}).text
    assert page.index("Newer Post") < page.index("Older Post")


def test_infinite_scroll(app_factory):
    app = app_factory()
    seed(app, [p(str(i), title=f"Job {i}") for i in range(51)])
    with TestClient(app) as c:
        first = c.get("/jobs").text
        more = c.get("/jobs", params={"offset": 50}, headers={"HX-Request": "true"}).text
    assert first.count('class="job-item') == 50 and 'hx-trigger="intersect once"' in first and "offset=50" in first
    assert more.count('class="job-item') == 1 and "intersect once" not in more and 'class="topbar"' not in more


def test_company_suggestions_escaped(app_factory):
    app = app_factory()
    seed(app, [p("1", company="A&B <Ltd>"), p("2", company="Beta")])
    with TestClient(app) as c:
        options = c.get("/companies", params={"company": "a&b"}).text
    assert '<option value="A&amp;B &lt;Ltd&gt;">' in options and "Beta" not in options


def test_lenient_query(app_factory):
    app = app_factory()
    seed(app, [p("1")])
    with TestClient(app) as c:
        assert c.get("/jobs", params={"tab": "x", "offset": "abc", "listing": "²", "sort": "?"}).status_code == 200
