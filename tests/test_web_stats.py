from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.models import ApplicationStep
from jobhunt.runner import run_daily
from jobhunt.timeutil import local_date, utcnow


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1"), p("2")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(sf, "1"), job_id(sf, "2")


def today(app):
    return local_date(utcnow(), app.state.settings.tz)


def test_empty_stats_page(app_factory):
    with TestClient(app_factory()) as c:
        html = c.get("/stats").text
        assert "No applications yet — mark a job Applied to start tracking." in html
        assert "Nothing in the last 12 weeks" in html
        assert '<a href="/stats" aria-current="page">Stats</a>' in html


def test_stats_page_sections_and_tabs(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, other = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        c.post(f"/jobs/{other}/status", data={"status": "hidden"})
        html = c.get("/stats").text
        for heading in ("Applications", "Pipeline", "Activity", "Breakdowns"):
            assert f">{heading}</h2>" in html
        assert f'id="app-row-{j}"' in html and "+1 this week" in html
        assert '<option value="" selected>Move to…</option>' in html
        assert f'id="app-row-{j}"' not in c.get("/stats?apps=closed").text
        assert f'id="app-row-{j}"' in c.get("/stats?apps=bogus").text


def test_stale_application_is_flagged(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, _ = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        with app.state.session_factory() as s:  # move the Applied step two weeks back
            step = s.exec(select(ApplicationStep)).one()
            step.on_date = today(app) - timedelta(days=14)
            s.add(step)
            s.commit()
        assert "no change for 14 days" in c.get("/stats").text


def test_stage_select_adds_a_step_and_returns_the_row(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, other = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        r = c.post(f"/jobs/{j}/steps", data={"stage": "offer", "on_date": today(app).isoformat(), "view": "stats-row"})
        assert r.status_code == 200 and r.text.lstrip().startswith(f'<tr id="app-row-{j}"')
        assert '<span class="tag stage-offer">Offer</span>' in r.text
        r = c.post(f"/jobs/{other}/steps", data={"stage": "offer", "on_date": today(app).isoformat(), "view": "stats-row"})
        assert r.status_code == 422 and "Mark the job Applied first" in r.text and f'id="app-row-{other}"' in r.text


def test_move_needs_a_button_press(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, _ = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        row = c.get("/stats").text.split(f'id="app-row-{j}"')[1].split("</tr>")[0]
        assert 'hx-trigger="change"' not in row  # arrow keys on a closed select fire change in Chrome
        assert f'<form class="move-form" hx-post="/jobs/{j}/steps"' in row
        assert f'<select id="move-{j}" name="stage" required' in row
        assert ">Move</button>" in row
