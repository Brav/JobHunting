from datetime import datetime

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.runner import run_daily


def seed(app, outcomes):
    clock = FakeClock(datetime(2026, 10, 28, 9, 0))
    run_daily(app.state.session_factory, FakeSource(outcomes), trigger="manual", clock=clock, sleep=clock.sleep)


def test_tracker_filters_and_cyrillic_search(app_factory):
    app = app_factory()
    a = add_listing(app.state.session_factory, "Python BG", keywords="python")
    add_listing(app.state.session_factory, "Go BG", keywords="go")
    seed(app, {"python": [[p("1", title="Програмер Python")]], "go": [[p("2", title="Go Developer")]]})
    with TestClient(app) as c:
        assert "2 jobs" in c.get("/jobs").text
        # the filter form submits empty values for "All listings" and an empty search box
        assert "2 jobs" in c.get("/jobs", params={"status": "default", "listing": "", "q": ""}).text
        r = c.get("/jobs", params={"q": "програмер"}).text
        assert "Програмер Python" in r and "Go Developer" not in r
        r = c.get("/jobs", params={"listing": a}).text
        assert "Програмер Python" in r and "Go Developer" not in r


def test_tracker_pagination_keeps_filters(app_factory):
    app = app_factory()
    add_listing(app.state.session_factory, "Python BG", keywords="python")
    seed(app, {"python": [[p(str(i), title=f"Job {i}") for i in range(51)]]})
    with TestClient(app) as c:
        first = c.get("/jobs", params={"q": "job"}).text
        assert "51 jobs" in first and "Next" in first and "page=2" in first and "q=job" in first
        second = c.get("/jobs", params={"q": "job", "page": 2}).text
        assert "Job 0" in second and "Previous" in second and "Job 50" not in second


def test_tracker_ignores_a_superscript_listing_id(app_factory):
    with TestClient(app_factory()) as c:
        assert c.get("/jobs", params={"listing": "²"}).status_code == 200
