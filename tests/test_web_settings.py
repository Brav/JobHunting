from fastapi.testclient import TestClient
from sqlmodel import select

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.timeutil import utcnow

FORM = {"work_rights": ["EU"], "home_country": "RS", "time_zone": "Europe/Belgrade", "relocate_to": ["EU"],
        "languages": ["en", "sr-group"], "contracts": ["employment", "b2b", "eor"]}


def test_settings_page_shows_the_profile(app_factory):
    with TestClient(app_factory()) as c:
        html = c.get("/settings").text
        assert 'name="work_rights" value="EU" checked' in html and 'name="languages" value="sr-group" checked' in html
        assert 'href="/settings" aria-label="Settings" aria-current="page"' in html


def test_saving_rechecks_jobs(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1", location="UK", is_remote=True)]]}),
                  trigger="manual", clock=clock, sleep=clock.sleep)
        r = c.post("/settings", data={**FORM, "work_rights": ["EU", "UK"]}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/settings?saved=1"
        with sf() as s:
            assert s.exec(select(Job.eligibility)).one() == "check"
        assert "Saved · 1 jobs re-checked" in c.get("/settings?saved=1").text


def test_settings_validation(app_factory):
    with TestClient(app_factory()) as c:
        for change, message in [({"languages": []}, "Pick at least one language"),
                                ({"home_country": "XX"}, "Unknown country"),
                                ({"time_zone": "Mars/Base"}, "Unknown time zone")]:
            r = c.post("/settings", data={**FORM, **change})
            assert r.status_code == 422 and message in r.text


def test_saving_a_zone_applies_it_at_once(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/settings", data={**FORM, "time_zone": "Asia/Tokyo"}, follow_redirects=False)
        assert r.status_code == 303
    assert app.state.settings.tz.key == "Asia/Tokyo"
    assert app.state.coordinator.settings.tz.key == "Asia/Tokyo"


def test_settings_offers_every_zone(app_factory):
    with TestClient(app_factory()) as c:
        html = c.get("/settings").text
        assert '<option value="America/Sao_Paulo">' in html and '<option value="Asia/Kolkata">' in html


def test_app_starts_in_the_profile_zone(tmp_path, app_factory):
    from dataclasses import replace
    from jobhunt.db import init_db, make_engine, make_session_factory
    from jobhunt.eligibility import DEFAULT_PROFILE, save_profile
    engine = make_engine(tmp_path / "web.db")
    init_db(engine)
    with make_session_factory(engine)() as s:
        save_profile(s, replace(DEFAULT_PROFILE, home_country="JP", time_zone="Asia/Tokyo"), utcnow())
    engine.dispose()
    assert app_factory(setup_done=False).state.settings.tz.key == "Asia/Tokyo"


def test_unsaved_profile_shows_the_server_zone(app_factory):
    with TestClient(app_factory(setup_done=False, env={"TZ": "America/Denver"})) as c:
        c.post("/setup/skip")
        assert 'name="time_zone" list="zones" value="America/Denver"' in c.get("/settings").text


# --- the CV section ---

PDF = b"%PDF-1.4\n% a tiny test file\n"


def test_upload_a_cv_in_settings(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/settings/cv", files={"cv": ("My CV.pdf", PDF, "application/pdf")}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/settings?cv=saved")
        html = c.get("/settings?cv=saved").text
        assert "My CV.pdf" in html and 'name="cv"' in html
    assert (app.state.settings.db_path.parent / "cv" / "cv.pdf").read_bytes() == PDF


def test_cv_upload_errors_show_on_the_page(app_factory):
    with TestClient(app_factory()) as c:
        r = c.post("/settings/cv", files={"cv": ("notes.txt", b"hello", "text/plain")})
        assert r.status_code == 422 and "Upload a PDF or Word (.docx) file" in r.text


def test_environment_cv_hides_the_upload(tmp_path, app_factory):
    env_cv = tmp_path / "Mine.pdf"
    env_cv.write_bytes(PDF)
    with TestClient(app_factory(cv_path=env_cv)) as c:
        html = c.get("/settings").text
        assert "Using the CV from your CV folder (set in .env)" in html and 'name="cv"' not in html


def test_upload_fills_empty_skills(app_factory):
    from test_cv import docx_bytes
    from jobhunt.preferences import load_preferences
    app = app_factory()
    with TestClient(app) as c:
        c.post("/settings/cv", files={"cv": ("cv.docx", docx_bytes("Backend developer", "Python, Django, PostgreSQL"),
                                             "application/octet-stream")})
    with app.state.session_factory() as s:
        assert "Python" in load_preferences(s).skills
