from datetime import timedelta
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.blocks import company_key
from jobhunt.models import Contact, ContactJob, Job
from jobhunt.runner import run_daily
from jobhunt.timeutil import local_date, utcnow


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1", company="Acme")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(sf, "1")


def contacts(app):
    with app.state.session_factory() as s:
        return list(s.exec(select(Contact).order_by(Contact.id)).all())


def test_contact_crud_and_due_first(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        today = local_date(utcnow(), app.state.settings.tz)
        c.post("/contacts", data={"name": "Zoe Later", "company": "Globex"})
        c.post("/contacts", data={"name": "Ana Due", "company": "Acme", "role": "Recruiter",
                                  "email": "ana@acme.example", "follow_up_on": (today - timedelta(days=1)).isoformat()})
        assert c.post("/contacts", data={"name": "  "}).status_code == 422
        page = c.get("/contacts").text
        assert page.index("Ana Due") < page.index("Zoe Later") and "Follow up" in page
        zoe = contacts(app)[0]
        c.post(f"/contacts/{zoe.id}", data={"name": "Zoe Now", "company": "Globex", "notes": "Met at a meetup"})
        assert contacts(app)[0].name == "Zoe Now" and "Met at a meetup" in c.get(f"/contacts/{zoe.id}").text
        c.post(f"/contacts/{zoe.id}/delete")
        assert [x.name for x in contacts(app)] == ["Ana Due"]


def test_link_contacts_to_a_job(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        r = c.post(f"/jobs/{j}/contacts", data={"name": "Ana Referrer", "relation": "referral", "role": "Engineer"})
        assert r.status_code == 200 and "Ana Referrer" in r.text and "Referral" in r.text
        with app.state.session_factory() as s:
            assert s.get(Job, j).applied_via == "referral"
            link = s.exec(select(ContactJob)).one()
        assert c.post(f"/jobs/{j}/contacts", data={"contact_id": str(link.contact_id), "relation": "pigeon"}).status_code == 400
        page = c.get(f"/companies/{quote(company_key('Acme'))}").text
        assert "Ana Referrer" in page
        r = c.post(f"/jobs/{j}/contacts/{link.contact_id}/unlink")
        assert "Ana Referrer" not in r.text.split("People")[1] if "People" in r.text else True
        with app.state.session_factory() as s:
            assert s.exec(select(ContactJob)).all() == []
        assert 'href="/contacts"' in c.get("/").text
