import pytest
from fastapi.testclient import TestClient
from sqlmodel import update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt import checks
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.scam import scam_flags
from jobhunt.timeutil import utcnow


def flags(description, title="Developer"):
    job = Job(external_id="x", title=title, company="c", location="", url="u", description=description)
    return [f["text"] for f in scam_flags(job)]


@pytest.mark.parametrize("text, flag", [
    ("A one-time registration fee of €49 secures your place.", "Asks you to pay a fee"),
    ("You will need to pay for your training materials up front.", "Asks you to pay a fee"),
    ("Salary is paid weekly in USDT.", "Pays in crypto or gift cards"),
    ("To apply, message our recruiter on WhatsApp.", "Contact on WhatsApp, Telegram or Signal"),
    ("Apply via Telegram and send your CV on WhatsApp.", "Contact on WhatsApp, Telegram or Signal"),
    ("Contact us on WhatsApp for an interview.", "Contact on WhatsApp, Telegram or Signal"),
    ("Telegram: +44 7700 900123", "Contact on WhatsApp, Telegram or Signal"),
    ("Send your CV to hiring.team.jobs@gmail.com", "Personal email address to apply"),
    ("No experience needed! Earn up to $500 per day.", "No experience needed, high pay"),
    ("You are paid per task: rate the apps and complete orders.", "Paid per task"),
    ("Work from your phone in your free time.", "Work from your phone"),
])
def test_rules(text, flag):
    assert flag in flags(text)


@pytest.mark.parametrize("text", [
    "There are no fees for candidates, ever.",
    "There is no registration fee.",
    "Experience with the WhatsApp Business API is a plus.",
    "Integrations: Gmail, Outlook and Slack.",
    "No experience needed with Kubernetes; we will teach you.",
    "Build a crypto payments platform used by banks.",
    "* Paid CPD Days\n* Professional Registration Fees Paid\n* RAVE Awards",  # a benefit, from a real job
    "We cover your training fees and certification costs.",
    "Registration fees are reimbursed in full.",
    # Ordinary tech jobs (from the final review).
    "Build payment infrastructure for crypto exchanges.",
    "You will own the payment gateway for Bitcoin and USDT merchants.",
    "Write integrations with the WhatsApp Business API.",
    "Our product connects chat platforms such as WhatsApp or Telegram.",
    "Application fee waived for all candidates.",
    "Maintain the application processing fee calculator.",
    "You will send notifications through WhatsApp and Telegram bots.",
    "Our mobile app lets users message each other on Signal-like encrypted chat.",
    "Work on your phone app: we build mobile banking.",
])
def test_look_alikes(text):
    assert flags(text) == []


def test_pane_and_row_show_the_warning(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1", title="Data Entry")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
        with sf() as s:
            s.exec(update(Job).values(description="Message us on WhatsApp. A starter kit costs €30."))
            s.commit()
            checks.refresh_all(s)
        assert "Scam warning signs" in c.get(f"/jobs/{job_id(sf, '1')}/pane").text
        assert "⚠" in c.get("/jobs").text
