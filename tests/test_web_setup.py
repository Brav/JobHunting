from fastapi.testclient import TestClient
from sqlmodel import select

from jobhunt.cv import current_cv
from jobhunt.eligibility import LEGACY_PROFILE, load_profile, profile_saved
from jobhunt.models import Listing

BANNER = "Finish setup so the app can tell which jobs you can take"
PDF = b"%PDF-1.4\n% a tiny test file\n"
SETUP = {"work_rights": ["US"], "home_country": "US", "time_zone": "America/Chicago", "languages": ["en"],
         "contracts": ["employment"]}
SEARCH = {"keywords": "data engineer", "location": "Chicago, United States", "radius_km": "25"}


def new_app(app_factory, **kw):
    app = app_factory(setup_done=False, **kw)
    runs = []
    app.state.coordinator.trigger = lambda trigger: runs.append(trigger) or True
    return app, runs


def test_pending_setup_redirects_pages(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        for path in ("/", "/jobs", "/settings"):
            r = c.get(path, follow_redirects=False)
            assert (r.status_code, r.headers["location"]) == (303, "/setup"), path


def test_exempt_paths_are_not_redirected(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        for path in ("/setup", "/static/style.css", "/capture", "/data/jobs.csv"):
            assert c.get(path, follow_redirects=False).status_code == 200, path


def test_skip_stops_the_redirect_and_shows_the_banner(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        r = c.post("/setup/skip", follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/")
        html = c.get("/", follow_redirects=False).text
        assert BANNER in html and 'href="/setup"' in html
        setup_page = c.get("/setup").text
        assert BANNER not in setup_page and "Skip for now" not in setup_page


def test_no_banner_once_setup_is_done(app_factory):
    with TestClient(app_factory()) as c:
        assert BANNER not in c.get("/").text


def test_setup_page_offers_skip_and_detects_the_zone(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        html = c.get("/setup").text
    assert "Skip for now" in html and "Save and start" in html and "data-detect" in html
    assert 'placeholder="City, Country"' in html and 'value="" selected>Choose your country' in html


def test_setup_saves_profile_and_search_and_starts_a_run(app_factory):
    app, runs = new_app(app_factory)
    with TestClient(app) as c:
        r = c.post("/setup", data={**SETUP, **SEARCH}, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/")
        assert c.get("/", follow_redirects=False).status_code == 200  # setup is done: no redirect, no banner
    with app.state.session_factory() as s:
        profile = load_profile(s)
        assert (profile.work_rights, profile.home_country, profile.time_zone, profile.languages, profile.contracts) == \
            (("US",), "US", "America/Chicago", ("en",), ("employment",))
        listing = s.exec(select(Listing)).one()
        assert (listing.name, listing.keywords, listing.location, listing.radius_km) == \
            ("data engineer", "data engineer", "Chicago, United States", 25)
    assert runs == ["setup"] and app.state.settings.tz.key == "America/Chicago"


def test_setup_without_a_search_starts_no_run(app_factory):
    app, runs = new_app(app_factory)
    with TestClient(app) as c:
        assert c.post("/setup", data=SETUP, follow_redirects=False).status_code == 303
    with app.state.session_factory() as s:
        assert profile_saved(s) and s.exec(select(Listing)).first() is None
    assert runs == []


def test_setup_errors_keep_what_was_typed(app_factory):
    app, runs = new_app(app_factory)
    with TestClient(app) as c:
        r = c.post("/setup", data={**SETUP, **SEARCH, "languages": []})
    assert r.status_code == 422 and "Pick at least one language" in r.text and 'value="data engineer"' in r.text
    with app.state.session_factory() as s:
        assert not profile_saved(s) and s.exec(select(Listing)).first() is None
    assert runs == []


def test_search_without_a_location_is_an_error(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        r = c.post("/setup", data={**SETUP, "keywords": "data engineer"})
    assert r.status_code == 422 and "Required." in r.text
    with app.state.session_factory() as s:
        assert not profile_saved(s)


def test_setup_with_a_cv(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        r = c.post("/setup", data=SETUP, files={"cv": ("My CV.pdf", PDF, "application/pdf")}, follow_redirects=False)
        assert r.status_code == 303
    with app.state.session_factory() as s:
        assert current_cv(s, app.state.settings).origin == "upload"


def test_setup_cv_error_saves_nothing(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        r = c.post("/setup", data={**SETUP, **SEARCH}, files={"cv": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 422 and "Upload a PDF or Word (.docx) file" in r.text and 'value="data engineer"' in r.text
    with app.state.session_factory() as s:
        assert not profile_saved(s) and current_cv(s, app.state.settings).path is None


def test_setup_shows_the_environment_cv(tmp_path, app_factory):
    env_cv = tmp_path / "Mine.pdf"
    env_cv.write_bytes(PDF)
    app, _ = new_app(app_factory, cv_path=env_cv)
    with TestClient(app) as c:
        html = c.get("/setup").text
    assert "Using the CV from your CV folder (set in .env)" in html and 'name="cv"' not in html


def test_legacy_zone_alias_is_accepted(app_factory):
    app, _ = new_app(app_factory)
    with TestClient(app) as c:
        assert c.post("/setup", data={**SETUP, "time_zone": "Asia/Calcutta"}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as s:
        assert load_profile(s).time_zone == "Asia/Calcutta"


def test_setup_after_done_shows_saved_values(app_factory):
    with TestClient(app_factory()) as c:
        html = c.get("/setup").text
    assert 'value="Europe/Belgrade"' in html and "Skip for now" not in html and "data-detect" not in html


def test_duplicate_first_search_saves_nothing(app_factory):
    app = app_factory()
    with app.state.session_factory() as s:
        s.add(Listing(name="x", keywords="x", location="Berlin"))
        s.commit()
    with TestClient(app) as c:
        r = c.post("/setup", data={**SETUP, "keywords": "x", "location": "Berlin, Germany", "radius_km": "25"})
    assert r.status_code == 422 and "A listing with this name already exists." in r.text
    with app.state.session_factory() as s:
        assert load_profile(s) == LEGACY_PROFILE
