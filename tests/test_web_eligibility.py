import pytest
from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.timeutil import utcnow
from jobhunt.web.common import job_tags


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1", location="London, England, UK", is_remote=True)]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(sf, "1")


def test_pane_shows_the_verdict_and_reasons(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j = seeded(app)
        html = c.get(f"/jobs/{j}/pane").text
        assert f'id="eligibility-{j}"' in html and "✕ Not eligible" in html
        assert "Job in the UK — no work rights there" in html and "Not right?" in html


def test_override_and_back_to_automatic(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j = seeded(app)
        r = c.post(f"/jobs/{j}/eligibility", data={"value": "eligible"})
        assert r.status_code == 200 and "set by you" in r.text and "✓ Eligible" in r.text
        assert f'id="job-item-{j}"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
        r = c.post(f"/jobs/{j}/eligibility", data={"value": "auto"})
        assert "set by you" not in r.text and "✕ Not eligible" in r.text
        assert c.post(f"/jobs/{j}/eligibility", data={"value": "maybe"}).status_code == 400
        assert c.post("/jobs/9999/eligibility", data={"value": "auto"}).status_code == 404


@pytest.mark.parametrize("values, tags", [
    (dict(eligibility="relocate", workplace="remote", language="de", status="new", seniority="Mid-Senior level"),
     [("↗ Relocate", "elig-relocate"), ("Remote", ""), ("German", "lang")]),
    (dict(eligibility="check", workplace=None, language="en", status="new", seniority="Mid-Senior level"),
     [("? Check", "elig-check"), ("Mid-Senior level", "")]),
    (dict(eligibility=None, eligibility_override="eligible", workplace="hybrid", language="en", status="interested"),
     [("✓ Eligible", "elig-eligible"), ("Hybrid", ""), ("Saved", "status-interested")]),
])
def test_row_tags_lead_with_eligibility(values, tags):
    job = Job(external_id="x", title="t", company="c", location="", url="u", **values)
    assert job_tags(job) == tags
