from datetime import datetime

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, FakeTranslator, add_listing, job_id, make_pdf, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.sources.base import JobPage, SourceBlocked


def make_cv(tmp_path):
    path = tmp_path / "cv.pdf"
    path.write_bytes(make_pdf([(72, 760, "Jane Doe"), (72, 740, "PHP Developer")]))
    return path


def seed_job(app) -> int:
    add_listing(app.state.session_factory, "PHP", keywords="php")
    clock = FakeClock(datetime(2026, 10, 28, 9, 0))
    run_daily(app.state.session_factory, FakeSource({"php": [[p("123", title="Senior PHP Developer")]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(app.state.session_factory, "123")


def test_cv_prompt_fetches_description_once(app_factory, tmp_path):
    calls = []

    def fetch_page(external_id):
        calls.append(external_id)
        return JobPage('Build <Laravel> APIs & "more".', [])

    translator = FakeTranslator()
    app = app_factory(fetch_page=fetch_page, translator=translator, cv_path=make_cv(tmp_path))
    jid = seed_job(app)
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}/cv-prompt").text
        c.get(f"/jobs/{jid}/cv-prompt")
    assert calls == ["123"] and translator.calls == []
    assert "Build &lt;Laravel&gt; APIs &amp; &#34;more&#34;." in page and "&lt;&lt;&lt;JOB DESCRIPTION" in page
    assert "Jane Doe" in page and 'id="prompt"' in page and "Copy prompt" in page and "https://claude.ai/new" in page
    with app.state.session_factory() as s:
        job = s.get(Job, jid)
    assert job.description == 'Build <Laravel> APIs & "more".' and job.description_fetched_at is not None


def test_cv_prompt_when_description_fails_then_try_again(app_factory, tmp_path):
    attempts = []

    def fetch_page(external_id):
        attempts.append(external_id)
        if len(attempts) == 1:
            raise SourceBlocked("rate limited by LinkedIn")
        return JobPage("Build APIs.", [])

    app = app_factory(fetch_page=fetch_page, cv_path=make_cv(tmp_path))
    jid = seed_job(app)
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}/cv-prompt").text
        assert "load the job description: rate limited by LinkedIn" in page and "Try again" in page
        assert "Description unavailable" in page and 'id="prompt"' in page
        r = c.post(f"/jobs/{jid}/description", data={"next": f"/jobs/{jid}/cv-prompt"}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, f"/jobs/{jid}/cv-prompt")
        assert "Build APIs." in c.get(f"/jobs/{jid}/cv-prompt").text
    assert len(attempts) == 2


def test_cv_prompt_without_cv(app_factory):
    app = app_factory()
    jid = seed_job(app)
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}/cv-prompt").text
    assert "CV not found at" in page and 'id="prompt"' not in page


def test_job_rows_link_to_cv_prompt_and_unknown_job_404(app_factory):
    app = app_factory()
    jid = seed_job(app)
    with TestClient(app) as c:
        assert f'href="/jobs/{jid}/cv-prompt"' in c.get(f"/jobs/{jid}/pane").text
        assert c.get("/jobs/9999/cv-prompt").status_code == 404
