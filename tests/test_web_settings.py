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


# --- the DeepL key ---

def test_settings_saves_the_key_and_switches(app_factory):
    app = app_factory()
    made = []
    app.state.translator_factory = lambda key: made.append(key) or f"translator for {key}"
    with TestClient(app) as c:
        r = c.post("/settings/translation", data={"action": "save", "key": " abc:fx \n"}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/settings?translation=saved")
        assert made == ["abc:fx"] and app.state.translator._inner == "translator for abc:fx"
        html = c.get("/settings?translation=saved").text
        assert "•••• c:fx" in html and "abc:fx" not in html
        r = c.post("/settings/translation", data={"action": "remove"}, follow_redirects=False)
        assert r.headers["location"] == "/settings?translation=removed"
        assert made == ["abc:fx", ""]


def test_empty_key_is_refused_on_the_page(app_factory):
    with TestClient(app_factory()) as c:
        r = c.post("/settings/translation", data={"action": "save", "key": "  "})
        assert r.status_code == 422 and "Paste the key first" in r.text


def test_environment_key_wins(app_factory):
    with TestClient(app_factory(env={"DEEPL_AUTH_KEY": "from-env:fx"})) as c:
        html = c.get("/settings").text
        assert "Using the key from .env" in html and 'name="key"' not in html
        assert c.post("/settings/translation", data={"action": "save", "key": "x"}).status_code == 409


def test_export_zip_never_holds_the_key(app_factory):
    import io
    import zipfile
    app = app_factory()
    app.state.translator_factory = lambda key: None
    with TestClient(app) as c:
        c.post("/settings/translation", data={"action": "save", "key": "abc-secret-key:fx"})
        data = c.get("/data/export.zip").content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() and all(b"abc-secret-key" not in z.read(name) for name in z.namelist())


# --- the default search schedule ---

def test_settings_shows_the_current_default(app_factory):
    with TestClient(app_factory()) as c:
        html = c.get("/settings").text
    assert "Search schedule" in html and "choice: 'every'" in html
    assert 'name="start" value="10:00"' in html and '<option value="4" selected>' in html
    assert 'name="schedule" value="default"' not in html.split('id="schedule"')[1].split("</section>")[0]


def test_saving_the_default_schedule(app_factory):
    from jobhunt.preferences import load_preferences
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/settings/schedule", data={"schedule": "times", "times": ["07:00", "19:00"]}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/settings?schedule=saved")
        assert "Schedule saved" in c.get("/settings?schedule=saved").text
    with app.state.session_factory() as s:
        prefs = load_preferences(s)
    assert prefs.schedule == "times:07:00,19:00" and prefs.schedule_changed_at


def test_default_schedule_errors(app_factory):
    with TestClient(app_factory()) as c:
        r = c.post("/settings/schedule", data={"schedule": "every", "start": "06:00", "every": "5"})
    assert r.status_code == 422 and "Choose how many hours" in r.text and 'name="start" value="06:00"' in r.text


def test_saving_the_default_moves_the_push(app_factory):
    app = app_factory()
    calls = []
    app.state.coordinator.schedule_changed = lambda: calls.append(1)
    with TestClient(app) as c:
        c.post("/settings/schedule", data={"schedule": "every", "start": "06:00", "every": "6"})
    assert calls == [1]


def test_a_once_a_day_default_shows_as_24_hours(app_factory):
    with TestClient(app_factory(env={"JOBHUNT_RUN_EVERY_HOURS": "24"})) as c:
        html = c.get("/settings").text
    assert '<option value="24" selected>' in html and '<option value="1" selected>' not in html


def test_a_bad_saved_change_time_doesnt_break_pages(app_factory):
    from dataclasses import replace
    from jobhunt.preferences import load_preferences, save_preferences
    app = app_factory()
    with app.state.session_factory() as s:
        save_preferences(s, replace(load_preferences(s), schedule_changed_at="yesterday"), utcnow())
    with TestClient(app) as c:
        assert c.get("/").status_code == 200 and c.get("/listings").status_code == 200
