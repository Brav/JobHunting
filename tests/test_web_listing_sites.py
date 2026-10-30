import json
import re

from fastapi.testclient import TestClient
from sqlmodel import select

from fakes import add_listing
from jobhunt.models import CustomFeed, Listing, WatchedCompany
from jobhunt.sites import default_sites_text, sites_text

FORM = {"name": "A", "keywords": "php remote", "location": "Belgrade, Serbia", "radius_km": "25"}


def _listing(app):
    with app.state.session_factory() as s:
        return s.exec(select(Listing)).one()


def test_create_on_default_and_on_chosen_sites(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        assert c.post("/listings", data=FORM, follow_redirects=False).status_code == 303
        assert _listing(app).sites is None
        c.post(f"/listings/{_listing(app).id}/delete")
        c.post("/listings", data={**FORM, "site_mode": "choose", "sites": ["devbg", "linkedin"]})
        assert json.loads(_listing(app).sites) == ["linkedin", "devbg"]


def test_choose_with_nothing_ticked_is_refused(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/listings", data={**FORM, "site_mode": "choose"})
        assert r.status_code == 400 and "Tick at least one site." in r.text
        with app.state.session_factory() as s:
            assert s.exec(select(Listing)).all() == []


def test_edit_shows_the_list_and_a_change_restarts_the_search_window(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        listing_id = add_listing(app.state.session_factory, "A", keywords="php", sites='["devbg"]')
        page = c.get(f"/listings/{listing_id}/edit").text
        assert re.search(r'name="site_mode" value="choose"[^>]*checked', page)
        assert re.search(r'name="sites" value="devbg"[^>]*checked', page)
        assert not re.search(r'name="sites" value="linkedin"[^>]*checked', page)
        same = {**FORM, "keywords": "php", "site_mode": "choose", "sites": ["devbg"]}
        c.post(f"/listings/{listing_id}", data=same)
        assert _listing(app).search_changed_at is None
        c.post(f"/listings/{listing_id}", data={**same, "sites": ["linkedin"]})
        assert _listing(app).search_changed_at is not None
        c.post(f"/listings/{listing_id}", data={**FORM, "keywords": "php"})
        assert _listing(app).sites is None


def test_unreadable_sites_show_as_default(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        listing_id = add_listing(app.state.session_factory, "A", sites="oops")
        assert re.search(r'name="site_mode" value="default"[^>]*checked', c.get(f"/listings/{listing_id}/edit").text)


def test_form_names_default_and_own_sites_and_the_table_shows_them(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        with sf() as s:
            assert default_sites_text(s) == "LinkedIn, Remote OK, Himalayas and Hacker News"
            s.add(CustomFeed(name="LaraJobs", url="https://larajobs.com/feed"))
            s.commit()
            assert default_sites_text(s) == "LinkedIn, Remote OK, Himalayas, Hacker News and your 1 site"
            s.add(WatchedCompany(company_key="acme", name="Acme", ats="lever", token="acme"))
            s.commit()
        add_listing(sf, "Mine", sites='["linkedin", "devbg", "hn"]')
        page = c.get("/listings").text
        assert "LinkedIn, Remote OK, Himalayas, Hacker News and your 2 sites" in page
        assert "LaraJobs" in page and "Acme" in page and "LinkedIn, DEV.BG +1" in page
        assert ("LinkedIn uses your keywords and location. The other sites match your keywords, ignoring place "
                "names, and cover the whole site.") in page
    assert sites_text(None, {}) == "Default" and sites_text('["devbg"]', {"devbg": "DEV.BG"}) == "DEV.BG"


def test_odd_site_mode_is_read_as_default_and_never_echoed(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        r = c.post("/listings", data={**FORM, "name": "", "site_mode": "default'};alert(1);//"})
        assert r.status_code == 400
        assert "alert(1)" not in r.text
        assert re.search(r'name="site_mode" value="default"[^>]*checked', r.text)
        assert not re.search(r'name="site_mode" value="choose"[^>]*checked', r.text)


def test_odd_schedule_choice_is_read_as_default_and_never_echoed(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        odd = {**FORM, "name": "", "schedule": "default'};alert(1);//"}
        r = c.post("/listings", data=odd)
        assert r.status_code == 400 and "alert(1)" not in r.text
        assert re.search(r'name="schedule" value="default"[^>]*checked', r.text)
        listing_id = add_listing(app.state.session_factory, "A")
        r = c.post(f"/listings/{listing_id}", data=odd)
        assert r.status_code == 400 and "alert(1)" not in r.text
        assert re.search(r'name="schedule" value="default"[^>]*checked', r.text)


def test_listings_page_is_not_linkedin_only(app_factory):
    with TestClient(app_factory()) as c:
        text = c.get("/listings").text
        assert "Your saved searches." in text
        assert "saved LinkedIn searches" not in text
