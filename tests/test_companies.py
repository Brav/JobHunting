from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlmodel import delete

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.blocks import company_key
from jobhunt.models import Sighting
from jobhunt.runner import run_daily
from jobhunt.timeutil import utcnow


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1", title="Backend Dev", company="Acme & Co"),
                                          p("2", title="Frontend Dev", company="ACME & co"),
                                          p("3", title="Other Dev", company="Globex")]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(sf, "1"), job_id(sf, "2"), job_id(sf, "3")


def test_company_pages(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        one, two, _ = seeded(app)
        c.post(f"/jobs/{one}/status", data={"status": "applied"})
        listing = c.get("/companies").text
        assert "Acme &amp; Co" in listing and "Globex" in listing
        url = f"/companies/{quote(company_key('Acme & Co'))}"
        assert f'href="{url}"' in listing
        page = c.get(url).text
        assert "Backend Dev" in page and "Frontend Dev" in page and "Other Dev" not in page
        assert "Applied 1× · 0 replies" in page and "Block company" in page and "seen in 2 search results" in page
        with app.state.session_factory() as s:  # jobs found before sightings were recorded have none
            s.exec(delete(Sighting))
            s.commit()
        assert "search result" not in c.get(url).text
        assert c.get("/companies/nobody-here").status_code == 404
        assert f'href="{url}"' in c.get(f"/jobs/{two}/pane").text
        assert 'href="/companies"' in c.get("/").text
