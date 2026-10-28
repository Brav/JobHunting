from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.applications import steps_for
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import job_tags


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1"), p("2")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(sf, "1"), job_id(sf, "2")


def today(app):
    return local_date(utcnow(), app.state.settings.tz).isoformat()


def steps(app, jid):
    with app.state.session_factory() as s:
        return steps_for(s, jid)


def test_application_block_appears_once_applied(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, _ = seeded(app)
        assert f'id="application-{j}"' not in c.get(f"/jobs/{j}/pane").text
        html = c.post(f"/jobs/{j}/status", data={"status": "applied"}).text
        assert f'id="application-{j}"' in html
        assert '<option value="hr_interview" selected>' in html
        assert f'value="{today(app)}" max="{today(app)}"' in html
        assert f'id="step-{steps(app, j)[0].id}"' in html
        assert f'id="application-{j}"' in c.get(f"/jobs/{j}").text  # standalone page too


def test_add_step_answers_with_pane_and_row(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, _ = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        r = c.post(f"/jobs/{j}/steps", data={"stage": "hr_interview", "on_date": today(app), "note": "call with Ana"})
        assert r.status_code == 200
        assert "call with Ana" in r.text
        assert f'id="job-item-{j}"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
        assert '<span class="tag stage-open">HR interview</span>' in r.text
        assert "HX-Trigger-After-Settle" not in r.headers


def test_step_errors_render_in_the_form(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, other = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        tomorrow = (local_date(utcnow(), app.state.settings.tz) + timedelta(days=1)).isoformat()
        for jid, data, message in [(j, {"stage": "offer", "on_date": tomorrow}, "The date can&#39;t be in the future"),
                                   (j, {"stage": "offer", "on_date": "soon"}, "Enter a valid date"),
                                   (other, {"stage": "offer", "on_date": today(app)}, "Mark the job Applied first")]:
            r = c.post(f"/jobs/{jid}/steps", data=data)
            assert r.status_code == 422 and message in r.text
        assert c.post("/jobs/9999/steps", data={"stage": "offer", "on_date": today(app)}).status_code == 404
        assert [st.stage for st in steps(app, j)] == ["applied"]


def test_delete_step(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, other = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        c.post(f"/jobs/{other}/status", data={"status": "applied"})
        c.post(f"/jobs/{j}/steps", data={"stage": "offer", "on_date": today(app)})
        newest = steps(app, j)[0]
        assert c.post(f"/jobs/{j}/steps/{steps(app, other)[0].id}/delete").status_code == 404
        r = c.post(f"/jobs/{j}/steps/{newest.id}/delete")
        assert r.status_code == 200 and f'id="step-{newest.id}"' not in r.text
        assert '<span class="tag stage-open">Applied</span>' in r.text


@pytest.mark.parametrize("stage, tag", [(None, ("Applied", "status-applied")), ("applied", ("Applied", "stage-open")),
                                        ("technical_interview", ("Technical interview", "stage-open")),
                                        ("offer", ("Offer", "stage-offer")), ("no_response", ("No response", "stage-closed"))])
def test_row_tag_shows_the_stage(stage, tag):
    job = Job(external_id="x", title="t", company="c", location="", url="u", status="applied", stage=stage)
    assert job_tags(job)[-1] == tag


def test_validation_answers_are_swapped(app_factory):
    with TestClient(app_factory()) as c:
        assert '{"code":"422","swap":true}' in c.get("/").text.replace(" ", "")
