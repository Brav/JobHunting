from fastapi.testclient import TestClient
from sqlmodel import select

from fakes import FakeSource, add_listing, make_posting as p
from jobhunt.models import Listing

FORM = {"name": "Python BG", "keywords": "python", "location": "Belgrade, Serbia", "radius_km": "25"}


def listings(app):
    with app.state.session_factory() as s:
        return s.exec(select(Listing)).all()


def test_create_and_show(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/listings", data=FORM, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/listings")
        page = c.get("/listings").text
    assert all(t in page for t in ("Python BG", "python", "Belgrade, Serbia", "25 km", "not run yet"))


def test_validation(app_factory):
    app = app_factory()
    add_listing(app.state.session_factory, "Python BG")
    with TestClient(app) as c:
        r = c.post("/listings", data={**FORM, "keywords": " "})
        assert r.status_code == 400 and "Required." in r.text
        r = c.post("/listings", data=FORM)
        assert r.status_code == 400 and "A listing with this name already exists." in r.text
        for bad in ("201", "abc", "-1", "²"):
            r = c.post("/listings", data={**FORM, "name": "Other", "radius_km": bad})
            assert r.status_code == 400 and "Radius must be a whole number from 0 to 200." in r.text
    assert len(listings(app)) == 1


def test_edit_toggle_delete(app_factory):
    app = app_factory()
    lid = add_listing(app.state.session_factory, "Python BG")
    with TestClient(app) as c:
        form = c.get(f"/listings/{lid}/edit")
        assert form.status_code == 200 and 'value="Python BG"' in form.text
        r = c.post(f"/listings/{lid}", data={**FORM, "keywords": "django"}, follow_redirects=False)
        assert r.status_code == 303
        assert listings(app)[0].keywords == "django"
        r = c.post(f"/listings/{lid}/toggle")
        assert r.status_code == 200 and "Off" in r.text and listings(app)[0].active is False
        assert c.post(f"/listings/{lid}/delete", follow_redirects=False).status_code == 303
    assert listings(app) == []


def test_special_characters_escaped_and_passed_through(app_factory):
    src = FakeSource({'"програмер" NOT senior': [[p("1")]]})
    app = app_factory(src)
    with TestClient(app) as c:
        c.post("/listings", data={**FORM, "name": 'C++ & "Go" <dev>', "keywords": '"програмер" NOT senior'})
        page = c.get("/listings").text
        app.state.coordinator.run_blocking("manual")
    assert "C++ &amp; &#34;Go&#34; &lt;dev&gt;" in page and "<dev>" not in page
    assert src.calls[0]["keywords"] == '"програмер" NOT senior'


def test_editing_search_fields_restarts_window(app_factory):
    app = app_factory()
    lid = add_listing(app.state.session_factory, "Python BG")
    with TestClient(app) as c:
        c.post(f"/listings/{lid}", data={**FORM, "name": "Renamed"})
        assert listings(app)[0].search_changed_at is None
        c.post(f"/listings/{lid}", data={**FORM, "name": "Renamed", "radius_km": "100"})
        assert listings(app)[0].search_changed_at is not None


def test_listing_shows_when_its_search_hit_the_limit(app_factory):
    app = app_factory(FakeSource({"python": [[p(str(i)) for i in range(100)]], "go": [[p("x")]]}))
    add_listing(app.state.session_factory, "Python BG", keywords="python")
    add_listing(app.state.session_factory, "Go BG", keywords="go")
    with TestClient(app) as c:
        app.state.coordinator.run_blocking("manual")
        page = c.get("/listings").text
    assert page.count("hit the limit of 100 jobs") == 1


def test_location_placeholder_is_neutral(app_factory):
    from fastapi.testclient import TestClient
    with TestClient(app_factory()) as c:
        html = c.get("/listings").text
    assert 'placeholder="City, Country"' in html and "Belgrade" not in html
