from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from fakes import FakeClock, FakeResponse, FakeSource, add_listing, add_run, make_posting as p, url_get
from jobhunt.models import Job
from jobhunt.preferences import save_feed_errors
from jobhunt.runner import run_daily
from jobhunt.sites import OwnSite, searched_sites
from jobhunt.sources.combined import CombinedSource
from jobhunt.timeutil import utcnow
from test_feeds import fake_get
from test_rss import WORDPRESS
from test_watchlist import LEVER

NOW = datetime(2026, 10, 28, 9, 0)


def test_only_the_listed_sites_are_read():
    calls = []
    primary = FakeSource({"php remote": [[p("1")]]})
    combined = CombinedSource(primary, get=fake_get(calls), clock=lambda: NOW)
    found = combined.search("php remote", "Belgrade", 25, 168, 100, sites=("remoteok",))
    assert primary.calls == [] and {x.source for x in found} == {"remoteok"}
    assert not any("himalayas" in url for url in calls)
    assert [x.external_id for x in combined.search("php remote", "Belgrade", 25, 168, 100)] == ["1"]  # None: LinkedIn only


def test_one_fetch_per_site_shared_by_listings_with_different_places():
    calls = []
    combined = CombinedSource(FakeSource({}), get=fake_get(calls), clock=lambda: NOW)
    for keywords in ("php remote sofia", "php remote plovdiv"):
        combined.search(keywords, "X", 25, 168, 100, sites=("himalayas", "remoteok"))
    assert sum("himalayas" in u for u in calls) == 1 and calls.count("https://remoteok.com/api") == 1


def test_a_per_keywords_site_is_not_asked_without_search_words():
    calls = []
    combined = CombinedSource(FakeSource({}), get=fake_get(calls), clock=lambda: NOW)
    assert combined.search("serbia srbija remote", "X", 25, 168, 100, sites=("himalayas",)) == []
    assert calls == [] and combined.errors == {}


def test_combined_source_adds_matches_and_survives_site_errors():
    calls = []
    primary = FakeSource({"php remote": [[p("1", title="LinkedIn PHP Dev")]]})
    combined = CombinedSource(primary, get=fake_get(calls), clock=lambda: NOW)
    found = combined.search("php remote", "europe", 200, 168, 100, sites=("linkedin", "remoteok", "himalayas"))
    assert {x.source for x in found} == {"linkedin", "remoteok", "himalayas"} and len(found) == 4
    combined.search("php remote", "europe", 200, 168, 100, sites=("linkedin", "remoteok", "himalayas"))
    assert calls.count("https://remoteok.com/api") == 1  # fetched once, then cached

    def broken(url, **kw):
        raise RuntimeError("down")
    failing = CombinedSource(FakeSource({"php remote": [[p("1")]]}), get=broken, clock=lambda: NOW)
    assert [x.external_id for x in failing.search("php remote", "europe", 200, 168, 100,
                                                  sites=("linkedin", "remoteok"))] == ["1"]
    assert "remoteok" in failing.errors


def test_errors_per_site_and_dropped_for_removed_sites():
    own = [OwnSite("feed:1", "Mine", "feed", "https://example.com/feed")]
    saved = []
    get = url_get({"https://example.com/robots.txt": FakeResponse(text=""), "https://example.com/feed": RuntimeError("down")})
    combined = CombinedSource(FakeSource({}), get=get, own=lambda: own, clock=lambda: NOW)
    combined.on_errors = saved.append
    combined.search("php", "X", 25, 168, 100, sites=("feed:1",))
    assert "down" in combined.errors["feed:1"]
    own.clear()
    combined.search("php", "X", 25, 168, 100, sites=("linkedin",))
    assert "feed:1" not in combined.errors and saved[-1] == {}


def test_own_feed_follows_robots_read_at_most_once_a_day():
    clock = FakeClock(NOW)
    answers = {"https://example.com/robots.txt": FakeResponse(text="User-agent: *\nDisallow:\n"),
               "https://example.com/feed": FakeResponse(content=WORDPRESS)}
    calls = []
    own = [OwnSite("feed:1", "Mine", "feed", "https://example.com/feed")]
    combined = CombinedSource(FakeSource({}), get=url_get(answers, calls), own=lambda: own, clock=clock,
                              ttl=timedelta(0))
    assert combined.search("php", "X", 25, 168, 100, sites=("feed:1",))
    combined.search("php", "X", 25, 168, 100, sites=("feed:1",))
    assert [c[0] for c in calls].count("https://example.com/robots.txt") == 1
    answers["https://example.com/robots.txt"] = FakeResponse(text="User-agent: *\nDisallow: /\n")
    clock.now += timedelta(hours=25)
    assert combined.search("php", "X", 25, 168, 100, sites=("feed:1",)) == []
    assert combined.errors["feed:1"] == "robots.txt now disallows it"


def test_a_board_site_reads_its_hiring_system():
    own = [OwnSite("board:4", "Acme", "lever", "https://jobs.lever.co/acme-co", "acme-co")]
    combined = CombinedSource(FakeSource({}), get=url_get({"https://api.lever.co/v0/postings/acme-co": LEVER}),
                              own=lambda: own, clock=lambda: NOW)
    assert {x.source for x in combined.search("php", "X", 25, 168, 100, sites=("board:4",))} == {"lever"}


def test_the_runner_passes_each_listings_sites(session_factory):
    add_listing(session_factory, "A", keywords="php")
    add_listing(session_factory, "B", keywords="php", sites='["devbg"]')
    source = FakeSource({})
    clock = FakeClock(NOW)
    run_daily(session_factory, source, trigger="manual", clock=clock, sleep=clock.sleep)
    assert [c["sites"] for c in source.calls] == [("linkedin", "remoteok", "himalayas", "hn"), ("devbg",)]


def test_a_listing_without_linkedin_still_records_its_feed_jobs(session_factory):
    add_listing(session_factory, "A", keywords="php remote", sites='["remoteok"]')
    primary = FakeSource({})
    clock = FakeClock(NOW)
    run_daily(session_factory, CombinedSource(primary, get=fake_get(), clock=lambda: NOW), trigger="manual",
              clock=clock, sleep=clock.sleep)
    with session_factory() as s:
        assert {j.source for j in s.exec(select(Job))} == {"remoteok"}
    assert primary.calls == []


def test_errors_shown_for_sites_active_listings_search(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="php", sites='["devbg"]')
        add_listing(sf, "B", keywords="php", sites='["infostud"]', active=False)
        add_run(sf, utcnow())  # the notice is on the report of a day with a run
        with sf() as s:
            save_feed_errors(s, {"devbg": "DEV.BG answered 503", "infostud": "Infostud answered 503"}, NOW)
            assert searched_sites(s) == {"devbg"}
        assert "DEV.BG isn't answering: DEV.BG answered 503" in c.get("/").text
        assert "Infostud answered 503" not in c.get("/settings").text
