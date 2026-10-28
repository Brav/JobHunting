from datetime import datetime

from fastapi.testclient import TestClient

from fakes import FakeClock, FakeSource, FakeTranslator, add_listing, job_id, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError
from jobhunt.translate import TranslationError, TranslationUnavailable

GERMAN = "Wir suchen einen erfahrenen PHP-Entwickler für unser Team in Berlin. Sie arbeiten mit Symfony."


def seed_job(app, title="Senior PHP Developer") -> int:
    add_listing(app.state.session_factory, "PHP", keywords="php")
    clock = FakeClock(datetime(2026, 10, 28, 9, 0))
    run_daily(app.state.session_factory, FakeSource({"php": [[p("123", title=title)]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    return job_id(app.state.session_factory, "123")


class Pages:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, external_id):
        self.calls.append(external_id)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_job_page_shows_whole_listing(app_factory):
    pages = Pages(JobPage("Build **PHP** APIs.\n\n<script>alert(1)</script>\n\n[click](javascript:alert(1))",
                          [("Seniority level", "Mid-Senior level")]))
    translator = FakeTranslator()
    app = app_factory(fetch_page=pages, translator=translator)
    jid = seed_job(app)
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
        c.get(f"/jobs/{jid}")
    assert pages.calls == ["123"] and translator.calls == []
    assert "<strong>PHP</strong>" in page and "&lt;script&gt;" in page and "<script>alert(1)" not in page
    assert 'href="javascript:' not in page
    assert "Seniority level" in page and "Mid-Senior level" in page and 'data-action="linkedin"' in page
    assert f'href="/jobs/{jid}/cv-prompt"' in page and 'data-action="save"' in page and 'id="job-pane"' in page


def test_job_page_translates_non_english(app_factory):
    app = app_factory(fetch_page=Pages(JobPage(GERMAN, [])), translator=FakeTranslator())
    jid = seed_job(app, title="Backend-Entwickler")
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
    assert "EN:Backend-Entwickler" in page and "Original title: Backend-Entwickler" in page
    assert f"EN:{GERMAN}" in page and "Show original (German)" in page and GERMAN in page


def test_job_page_translation_failure_shows_original(app_factory):
    app = app_factory(fetch_page=Pages(JobPage(GERMAN, [])),
                      translator=FakeTranslator(fail=TranslationError("DeepL error: boom")))
    jid = seed_job(app, title="Backend-Entwickler")
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
    assert "DeepL error: boom" in page and GERMAN in page and "Show original" not in page


def test_job_page_download_failure_then_try_again(app_factory):
    pages = Pages(SourceError("job page not available on LinkedIn"), JobPage("Build APIs.", []))
    app = app_factory(fetch_page=pages)
    jid = seed_job(app)
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
        assert "load the job listing: job page not available on LinkedIn" in page and "Try again" in page
        r = c.post(f"/jobs/{jid}/description", data={"next": f"/jobs/{jid}"}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, f"/jobs/{jid}")
        assert "Build APIs." in c.get(f"/jobs/{jid}").text
        r = c.post(f"/jobs/{jid}/description", data={"next": "https://evil.example/"}, follow_redirects=False)
        assert r.headers["location"] == f"/jobs/{jid}"
    assert len(pages.calls) == 3  # GET, Try again, and the last POST; the second GET used the stored listing


def test_job_rows_show_translated_title_and_language(app_factory):
    app = app_factory()
    jid = seed_job(app, title="Backend-Entwickler")
    with app.state.session_factory() as s:
        job = s.get(Job, jid)
        job.language, job.title_en = "de", "Backend Developer"
        s.add(job)
        s.commit()
    with TestClient(app) as c:
        page = c.get("/").text
        assert 'class="item-title" title="Backend-Entwickler">Backend Developer</span>' in page
        assert '<span class="tag lang">German</span>' in page
        assert "Backend Developer" in c.get("/jobs", params={"q": "backend developer"}).text
        assert c.get("/jobs/9999").status_code == 404


def test_job_page_buttons_update_the_pane(app_factory):
    app = app_factory()
    jid = seed_job(app)
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
        r = c.post(f"/jobs/{jid}/status", data={"status": "hidden"})
    assert 'hx-target="#job-pane"' in page and "Back" in page
    assert r.status_code == 200 and f'id="pane-title-{jid}"' in r.text and "Reset" in r.text
    assert "HX-Trigger-After-Settle" in r.headers


def test_labels_say_save(app_factory):
    app = app_factory()
    jid = seed_job(app)
    with TestClient(app) as c:
        pane = c.get(f"/jobs/{jid}/pane").text
        assert 'data-action="save"' in pane and "Save <kbd>S</kbd>" in pane
        c.post(f"/jobs/{jid}/status", data={"status": "interested", "view": "pane"})
        pages = [c.get(url).text for url in ("/", "/jobs", f"/jobs/{jid}", "/listings")]
    assert '<span class="tag status-interested">Saved</span>' in pages[1]
    assert all("Interested" not in page for page in pages)

def test_identical_translated_title_is_not_repeated(app_factory):
    app = app_factory()
    jid = seed_job(app, title="Senior Full-Stack Developer (m/w/d)")
    with app.state.session_factory() as s:
        job = s.get(Job, jid)
        job.language, job.title_en, job.description = "de", "Senior Full-Stack Developer (m/w/d)", "Text."
        s.add(job)
        s.commit()
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
        row_page = c.get("/jobs").text
    assert "Original title" not in page and 'title="Senior Full-Stack Developer (m/w/d)"' not in row_page


def test_failed_try_again_sends_one_request(app_factory):
    pages = Pages(SourceBlocked("rate limited by LinkedIn"))
    app = app_factory(fetch_page=pages)
    jid = seed_job(app)
    with TestClient(app) as c:
        c.get(f"/jobs/{jid}")
        page = c.post(f"/jobs/{jid}/description", data={"next": f"/jobs/{jid}"}).text
    assert len(pages.calls) == 2 and "load the job listing: rate limited by LinkedIn" in page


def test_unavailable_translation_is_shown_but_not_stored(app_factory):
    app = app_factory(fetch_page=Pages(JobPage(GERMAN, [])),
                      translator=FakeTranslator(fail=TranslationUnavailable("DeepL error: timeout")))
    jid = seed_job(app, title="Backend-Entwickler")
    with TestClient(app) as c:
        page = c.get(f"/jobs/{jid}").text
    assert "DeepL error: timeout" in page and GERMAN in page
    with app.state.session_factory() as s:
        assert s.get(Job, jid).translation_error is None


def test_pane_downloads_respect_linkedin_backoff(app_factory):
    pages = Pages(SourceBlocked("rate limited by LinkedIn"))
    app = app_factory(fetch_page=pages)
    add_listing(app.state.session_factory, "PHP", keywords="php")
    clock = FakeClock(datetime(2026, 10, 28, 9, 0))
    run_daily(app.state.session_factory, FakeSource({"php": [[p("1"), p("2")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    one, two = job_id(app.state.session_factory, "1"), job_id(app.state.session_factory, "2")
    with TestClient(app) as c:
        first = c.get(f"/jobs/{one}/pane").text
        second = c.get(f"/jobs/{two}/pane").text
    assert "rate limited by LinkedIn" in first and "LinkedIn is limiting requests" in second
    assert len(pages.calls) == 1                                  # the second pane didn't ask LinkedIn
    assert app.state.coordinator.linkedin_blocked()               # and the background pass backs off too
