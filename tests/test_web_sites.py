import json

from fakes import FakeResponse, add_listing, url_get
from fastapi.testclient import TestClient
from sqlmodel import select
from test_rss import WORDPRESS
from test_site_check import OPEN, PUBLIC

from jobhunt.blocks import company_key
from jobhunt.models import CustomFeed, Listing, WatchedCompany

FEED_ROUTES = {"https://example.com/robots.txt": OPEN, "https://example.com/feed": FakeResponse(content=WORDPRESS)}


def client(app_factory, routes):
    app = app_factory()
    app.state.http_get = url_get(routes)
    app.state.resolve = PUBLIC
    return app, TestClient(app)


def test_page_lists_builtin_and_own_sites_and_the_limits(app_factory):
    app, c = client(app_factory, {})
    with app.state.session_factory() as s:
        s.add(CustomFeed(name="LaraJobs", url="https://larajobs.com/feed"))
        s.commit()
    page = c.get("/sites").text
    for text in ("DEV.BG", "IT jobs in Bulgaria", "LaraJobs", "https://larajobs.com/feed", "What can't be added"):
        assert text in page
    assert '<a href="/sites"' in c.get("/").text


def test_preview_then_save_a_feed(app_factory):
    app, c = client(app_factory, FEED_ROUTES)
    r = c.post("/sites/preview", data={"url": "https://example.com/feed", "name": ""})
    assert r.status_code == 200 and "PHP Developer" in r.text and "I've checked this site's terms allow reading it" in r.text
    form = {"kind": "feed", "url": "https://example.com/feed", "name": "Example jobs", "token": ""}
    r = c.post("/sites/add", data=form)
    assert r.status_code == 400 and "Tick the box to confirm the site's terms allow reading it." in r.text
    assert c.post("/sites/add", data={**form, "terms": "on"}, follow_redirects=False).status_code == 303
    with app.state.session_factory() as s:
        assert [(f.name, f.url) for f in s.exec(select(CustomFeed))] == [("Example jobs", "https://example.com/feed")]
    r = c.post("/sites/add", data={**form, "terms": "on"})
    assert r.status_code == 400 and "You've already added this site." in r.text


def test_a_refusal_shows_its_reason_and_saves_nothing(app_factory):
    routes = {**FEED_ROUTES, "https://example.com/robots.txt": FakeResponse(text="User-agent: *\nDisallow: /\n")}
    app, c = client(app_factory, routes)
    r = c.post("/sites/preview", data={"url": "https://www.linkedin.com/jobs/search", "name": ""})
    assert "This site's terms forbid automated reading." in r.text
    form = {"kind": "feed", "url": "https://example.com/feed", "name": "X", "token": "", "terms": "on"}
    assert c.post("/sites/add", data=form).status_code == 400  # saving re-checks robots.txt
    with app.state.session_factory() as s:
        assert s.exec(select(CustomFeed)).all() == []


def test_saving_a_careers_page_replaces_the_companys_old_board(app_factory):
    app, c = client(app_factory, {"https://jobs.lever.co/robots.txt": OPEN})
    with app.state.session_factory() as s:
        s.add(WatchedCompany(company_key=company_key("Acme"), name="Acme", ats="greenhouse", token="acme"))
        s.commit()
    form = {"kind": "lever", "url": "https://jobs.lever.co/acme", "name": "Acme", "token": "acme", "terms": "on"}
    assert c.post("/sites/add", data=form, follow_redirects=False).status_code == 303
    with app.state.session_factory() as s:
        (watch,) = s.exec(select(WatchedCompany)).all()
        assert (watch.id, watch.ats, watch.token) == (1, "lever", "acme")


def test_removing_a_feed_sends_its_only_listings_back_to_default(app_factory):
    app, c = client(app_factory, {})
    sf = app.state.session_factory
    with sf() as s:
        feed = CustomFeed(name="LaraJobs", url="https://larajobs.com/feed")
        s.add(feed)
        s.commit()
    add_listing(sf, "A", sites=json.dumps([f"feed:{feed.id}"]))
    r = c.post(f"/sites/feeds/{feed.id}/remove")
    assert "Removed LaraJobs. 1 listing went back to Default sites." in r.text
    with sf() as s:
        assert s.exec(select(Listing)).one().sites is None


def test_unwatching_a_company_drops_its_key_from_listings(app_factory):
    app, c = client(app_factory, {})
    sf = app.state.session_factory
    with sf() as s:
        watch = WatchedCompany(company_key="acme", name="Acme", ats="lever", token="acme")
        s.add(watch)
        s.commit()
    add_listing(sf, "A", sites=json.dumps([f"board:{watch.id}", "hn"]))
    c.post("/companies/acme/unwatch")
    with sf() as s:
        assert json.loads(s.exec(select(Listing)).one().sites) == ["hn"]


def test_names_and_reasons_are_escaped_and_a_bad_link_is_not_linked(app_factory):
    app, c = client(app_factory, {})
    with app.state.session_factory() as s:
        s.add(CustomFeed(name="<script>x()</script>", url="javascript:alert(1)"))
        s.commit()
    page = c.get("/sites?added=%3Cb%3Eboom%3C%2Fb%3E").text
    assert "<script>x()" not in page and "<b>boom" not in page and "&lt;b&gt;boom" in page
    assert 'href="javascript:' not in page
