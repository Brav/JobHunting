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


# --- when each listing runs ---

def one_listing(app):
    (listing,) = listings(app)
    return listing


def test_new_listing_with_its_own_times(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/listings", data={**FORM, "schedule": "times", "times": ["13:00", "8:00"]}, follow_redirects=False)
        assert r.status_code == 303
    assert one_listing(app).schedule == "times:08:00,13:00"


def test_new_listing_on_the_default(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        c.post("/listings", data={**FORM, "schedule": "default", "times": ["09:00"]})
    assert one_listing(app).schedule is None


def test_listing_every_n_hours(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        c.post("/listings", data={**FORM, "schedule": "every", "start": "00:00", "every": "6"})
    assert one_listing(app).schedule == "every:00:00/6"


def test_schedule_errors_keep_the_form(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/listings", data={**FORM, "schedule": "times", "times": ["8:00", "08:00"]})
    assert r.status_code == 400 and "A time appears twice" in r.text and 'value="Python BG"' in r.text
    assert listings(app) == []


def test_editing_other_fields_keeps_schedule_changed_at(app_factory):
    app = app_factory()
    lid = add_listing(app.state.session_factory, "Python BG")
    with TestClient(app) as c:
        c.post(f"/listings/{lid}", data={**FORM, "keywords": "django", "schedule": "default"})
    listing = one_listing(app)
    assert (listing.keywords, listing.schedule, listing.schedule_changed_at) == ("django", None, None)


def test_editing_the_schedule_sets_schedule_changed_at(app_factory):
    app = app_factory()
    lid = add_listing(app.state.session_factory, "Python BG")
    with TestClient(app) as c:
        c.post(f"/listings/{lid}", data={**FORM, "schedule": "times", "times": ["07:00"]})
    listing = one_listing(app)
    assert listing.schedule == "times:07:00" and listing.schedule_changed_at is not None


def test_switching_on_sets_schedule_changed_at(app_factory):
    app = app_factory()
    lid = add_listing(app.state.session_factory, "Python BG", active=False)
    with TestClient(app) as c:
        c.post(f"/listings/{lid}/toggle")
    listing = one_listing(app)
    assert listing.active and listing.schedule_changed_at is not None


def test_listings_page_describes_each_schedule(app_factory):
    app = app_factory()
    sf = app.state.session_factory
    for name, schedule in (("A", "times:08:00,13:00"), ("B", "every:00:00/6"), ("C", None)):
        lid = add_listing(sf, name)
        with sf() as s:
            row = s.get(Listing, lid)
            row.schedule = schedule
            s.add(row)
            s.commit()
    with TestClient(app) as c:
        page = c.get("/listings").text
    assert all(t in page for t in ("08:00, 13:00", "Every 6 h from 00:00", "Default · every 4 h from 10:00"))


def test_form_offers_the_three_choices(app_factory):
    with TestClient(app_factory()) as c:
        page = c.get("/listings").text
    for text in ("When it runs", "Default schedule (Every 4 h from 10:00)", "At these times",
                 "From a start time, every N hours", "+ Add a time",
                 "More frequent searches make LinkedIn blocks more likely.", '<option value="12"'):
        assert text in page, text
    assert '<option value="5"' not in page


def test_edit_page_shows_the_listings_times(app_factory):
    app = app_factory()
    lid = add_listing(app.state.session_factory, "Python BG")
    with TestClient(app) as c:
        c.post(f"/listings/{lid}", data={**FORM, "schedule": "times", "times": ["07:00", "19:30"]})
        page = c.get(f"/listings/{lid}/edit").text
    assert "choice: 'times'" in page and "[&#34;07:00&#34;, &#34;19:30&#34;]" in page  # escaped inside x-data="…"
