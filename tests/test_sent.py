from fastapi.testclient import TestClient
from sqlmodel import select, update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.stats import breakdowns, load_applications
from jobhunt.timeutil import utcnow


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1"), p("2")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(sf, "1"), job_id(sf, "2")


def test_apply_link(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, _ = seeded(app)
        with app.state.session_factory() as s:
            s.exec(update(Job).where(Job.id == j).values(apply_url="https://jobs.acme.com/apply/42", description="Build APIs."))
            s.commit()
        html = c.get(f"/jobs/{j}/pane").text
        assert 'href="https://jobs.acme.com/apply/42"' in html and "Apply on company site" in html


def test_what_i_sent(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        j, other = seeded(app)
        c.post(f"/jobs/{j}/status", data={"status": "applied"})
        r = c.post(f"/jobs/{j}/sent", data={"applied_via": "company_site", "sent_cv": "CV tailored for Acme",
                                             "sent_tailored": "1", "sent_cover_letter": "1"},
                   files={"file": ("../../evil cv.pdf", b"%PDF-1.4 sent", "application/pdf")})
        assert r.status_code == 200 and "CV tailored for Acme" in r.text
        with app.state.session_factory() as s:
            job = s.get(Job, j)
        assert (job.applied_via, job.sent_tailored, job.sent_cover_letter) == ("company_site", True, True)
        stored = app.state.settings.db_path.parent / job.sent_file
        assert stored.parent.name == str(j) and stored.name == "evil_cv.pdf" and stored.read_bytes() == b"%PDF-1.4 sent"
        assert c.get(f"/jobs/{j}/sent-file").content == b"%PDF-1.4 sent"
        assert c.post(f"/jobs/{j}/sent", data={"applied_via": "pigeon"}).status_code == 400
        assert c.post(f"/jobs/{j}/sent", data={"applied_via": "email"},
                      files={"file": ("cv.exe", b"MZ", "application/octet-stream")}).status_code == 400
        assert c.get(f"/jobs/{other}/sent-file").status_code == 404
        c.post(f"/jobs/{other}/status", data={"status": "applied"})
        with app.state.session_factory() as s:
            b = breakdowns(load_applications(s))
        rows = {k: [(r.label, r.applied) for r in v] for k, v in b.items()}
        assert rows["Channel"] == [("Company site", 1), ("Not recorded", 1)]
        assert rows["CV"] == [("Not recorded", 1), ("Tailored", 1)]
        assert rows["Cover letter"] == [("Not recorded", 1), ("With cover letter", 1)]
        stats_page = c.get("/stats").text
        assert "<caption>By CV</caption>" in stats_page and "<caption>By cover letter</caption>" in stats_page
