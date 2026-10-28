import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from jobhunt.email_update import candidates, classify, note_for, proposed_stage
from jobhunt.models import ApplicationStep, Job


def add(s, company, status):
    job = Job(external_id=company, title="Developer", company=company, location="Remote", url="u", status=status)
    s.add(job)
    s.commit()
    return job.id


@pytest.mark.parametrize("text, kind, phrase", [
    ("Thank you for applying to Acme. We have received your application and will review it.", "received",
     "Thank you for applying"),
    ("Thank you for your application. Unfortunately, we have decided to move forward with other candidates.",
     "rejected", "Unfortunately"),
    ("After careful consideration we will not be moving forward with your application.", "rejected",
     "not be moving forward"),
    ("We regret to inform you that the position has been filled.", "rejected", "regret to inform you"),
    ("Thanks for applying! We'd like to invite you to a technical interview. Please share your availability.",
     "interview", "invite you to a technical interview"),
    ("We'd love to move forward and schedule a call with our CTO.", "interview", "schedule a call"),
    ("We are pleased to offer you the position of Senior Developer.", "offer", "pleased to offer you"),
    ("Leider müssen wir Ihnen mitteilen, dass wir uns für einen anderen Kandidaten entschieden haben.", "rejected",
     "Leider"),
    ("Wir möchten Sie gerne zu einem Vorstellungsgespräch einladen.", "interview", "Vorstellungsgespräch"),
    ("Vielen Dank für Ihre Bewerbung, wir haben sie erhalten.", "received", "Vielen Dank für Ihre Bewerbung"),
    ("Our newsletter: ten tips for your CV.", None, None),
])
def test_classify(text, kind, phrase):
    assert classify(text) == (kind, phrase)


def test_proposed_stage():
    assert [proposed_stage("interview", s) for s in (None, "applied", "hr_interview", "final_interview")] == \
        ["hr_interview", "hr_interview", "technical_interview", "final_interview"]
    assert (proposed_stage("rejected", "hr_interview"), proposed_stage("offer", "final_interview")) == ("rejected", "offer")
    assert (proposed_stage("received", None), proposed_stage("received", "applied")) == ("applied", None)
    assert proposed_stage(None, "applied") is None


def test_candidates_and_note(session_factory):
    with session_factory() as s:
        acme = add(s, "Acme GmbH", "applied")
        globex = add(s, "Globex Corporation", "interested")
        add(s, "Initech", "new")
        add(s, "Hooli", "hidden")
        assert [j.id for j in candidates(s, "From: Jobs <jobs@acme.de>\nSubject: Your application\n\nHello")] == [acme]
        assert [j.id for j in candidates(s, "Thanks for your interest in Globex! We will be in touch.")] == [globex]
        assert candidates(s, "Initech and Hooli are hiring") == []
    assert note_for("From: x@acme.de\nSubject: Re: Your application\n\nHi") == "From email: Re: Your application"
    assert note_for("\n\n  Dear Alex,\nthanks") == "From email: Dear Alex,"


def test_email_flow(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        with app.state.session_factory() as s:
            acme = add(s, "Acme GmbH", "interested")
        email = "From: Jobs <jobs@acme.de>\nSubject: Interview invitation\n\nWe'd like to invite you to an interview."
        r = c.post("/email", data={"text": email})
        assert r.status_code == 200 and "invite you to an interview" in r.text
        assert '<option value="hr_interview" selected>' in r.text
        with app.state.session_factory() as s:
            assert s.exec(select(ApplicationStep)).all() == []  # nothing is stored before Confirm
        token = r.text.split('name="token" value="')[1].split('"')[0]
        form = {"job_id": acme, "stage": "hr_interview", "note": "From email: Interview invitation"}
        assert c.post("/email/confirm", data=form).status_code == 403
        r = c.post("/email/confirm", data={**form, "token": token}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/jobs/{acme}"
        with app.state.session_factory() as s:
            job = s.get(Job, acme)
            steps = s.exec(select(ApplicationStep).order_by(ApplicationStep.id)).all()
        assert (job.status, job.stage) == ("applied", "hr_interview")
        assert [(st.stage, st.note) for st in steps] == [("applied", ""), ("hr_interview", "From email: Interview invitation")]
        assert "No saved or applied job" in c.post("/email", data={"text": "Hello from Umbrella"}).text
