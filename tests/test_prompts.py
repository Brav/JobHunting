from datetime import date

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, add_listing, job_id, make_pdf, make_posting as p
from jobhunt.models import ApplicationStep, Contact, Job, Question
from jobhunt.prompts import KINDS, build_prompt
from jobhunt.runner import run_daily
from jobhunt.timeutil import utcnow

CV = "Alex Example, PHP developer, Laravel, 8 years"


def job():
    return Job(id=1, external_id="x", title="Senior PHP Developer", company="Acme", location="Remote",
               url="https://example.com/1", description="Build Laravel APIs.", status="applied", stage="hr_interview")


def test_prompts_carry_the_job_and_the_cv():
    steps = [ApplicationStep(job_id=1, stage="applied", on_date=date(2026, 10, 1)),
             ApplicationStep(job_id=1, stage="hr_interview", on_date=date(2026, 10, 8), note="call with Ana")]
    people = [(Contact(name="Ana Recruiter", role="Recruiter", email="ana@acme.example"), "recruiter"),
              (Contact(name="Bo Friend", role="Engineer"), "referral")]
    questions = [Question(text="Tell me about a hard bug", company=""), Question(text="Why Acme?", company="Acme"),
                 Question(text="Why Globex?", company="Globex")]
    for kind in KINDS:
        text = build_prompt(kind, job(), CV, steps, people, questions)
        assert "Senior PHP Developer" in text and "Acme" in text and CV in text and "<<<JOB DESCRIPTION" in text
    interview = build_prompt("interview", job(), CV, steps, people, questions)
    assert "HR interview" in interview and "call with Ana" in interview
    assert "Why Acme?" in interview and "Tell me about a hard bug" in interview and "Why Globex?" not in interview
    follow = build_prompt("follow_up", job(), CV, steps, people, questions)
    assert "2026-10-01" in follow and "ana@acme.example" in follow
    assert "Bo Friend" in build_prompt("referral", job(), CV, steps, people, questions)


def test_prompt_pages_and_question_bank(app_factory, tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(make_pdf([(72, 720, CV)]))
    app = app_factory(cv_path=cv)
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1", company="Acme")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
        j = job_id(sf, "1")
        c.post("/questions", data={"text": "Why Acme?", "company": "Acme"})
        page = c.get(f"/jobs/{j}/prompts/interview").text
        assert "Interview preparation" in page and "Why Acme?" in page
        assert c.get(f"/jobs/{j}/prompts/poem").status_code == 404
        bank = c.get("/questions").text
        assert "Why Acme?" in bank
        qid = bank.split('action="/questions/')[1].split("/")[0]
        c.post(f"/questions/{qid}/delete")
        assert "Why Acme?" not in c.get("/questions").text
        assert f'href="/jobs/{j}/prompts/cover_letter"' in c.get(f"/jobs/{j}/pane").text
