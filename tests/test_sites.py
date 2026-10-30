import json

from sqlmodel import select

from jobhunt.models import CustomFeed, Job, Listing, Setting, WatchedCompany
from jobhunt.preferences import (Preferences, load_feed_errors, load_preferences, save_feed_errors,
                                 save_preferences)
from jobhunt.sites import (BUILTIN, SOURCE_LABELS, OwnSite, own_sites, parse_sites, remove_site, site_labels,
                           sites_for)
from jobhunt.timeutil import utcnow

DEFAULT = ("linkedin", "remoteok", "himalayas", "hn")


def _raw_prefs(s, data):
    s.merge(Setting(key="preferences", value=json.dumps(data)))
    s.commit()


def test_saved_preferences_without_sites_fall_back_to_linkedin_and_the_old_sources(session_factory):
    with session_factory() as s:
        assert load_preferences(s).sites == DEFAULT
        _raw_prefs(s, {"sources": ["remoteok", "hn"]})
        assert load_preferences(s).sites == ("linkedin", "remoteok", "hn")
        _raw_prefs(s, {"sources": []})
        assert load_preferences(s).sites == ("linkedin",)
        _raw_prefs(s, {"sites": ["devbg"]})
        assert load_preferences(s).sites == ("devbg",)
        _raw_prefs(s, {"sites": []})
        assert load_preferences(s).sites == DEFAULT


def test_parse_sites():
    known = {"linkedin", "devbg", "feed:1"}
    assert parse_sites(None, known) is None
    assert parse_sites("not json", known) is None
    assert parse_sites('{"a": 1}', known) is None
    assert parse_sites('["gone", "nope"]', known) is None
    assert parse_sites('["devbg", "gone", "linkedin"]', known) == ("devbg", "linkedin")


def test_sites_for_default_and_own_lists(session_factory):
    with session_factory() as s:
        feed = CustomFeed(name="LaraJobs", url="https://larajobs.com/feed")
        board = WatchedCompany(company_key="acme", name="Acme", ats="lever", token="acme")
        s.add_all([feed, board])
        s.commit()
        own = (f"feed:{feed.id}", f"board:{board.id}")
        listing = Listing(name="A", keywords="php", location="Belgrade")
        assert sites_for(s, listing) == DEFAULT + own
        save_preferences(s, Preferences(sites=("devbg",)), utcnow())
        assert sites_for(s, listing) == ("devbg",) + own
        listing.sites = '["hn", "feed:999"]'
        assert sites_for(s, listing) == ("hn",)
        listing.sites = '["feed:999"]'
        assert sites_for(s, listing) == ("devbg",) + own  # nothing known left: Default sites


def test_own_sites_and_labels(session_factory):
    with session_factory() as s:
        feed = CustomFeed(name="LaraJobs", url="https://larajobs.com/feed")
        board = WatchedCompany(company_key="acme", name="Acme", ats="lever", token="acme")
        s.add_all([feed, board])
        s.commit()
        assert own_sites(s) == [OwnSite(f"feed:{feed.id}", "LaraJobs", "feed", "https://larajobs.com/feed"),
                                OwnSite(f"board:{board.id}", "Acme", "lever", "https://jobs.lever.co/acme", "acme")]
        labels = site_labels(s)
        assert labels["devbg"] == "DEV.BG" and labels[f"feed:{feed.id}"] == "LaraJobs"


def test_remove_site_drops_its_key_and_sends_empty_listings_back_to_default(session_factory):
    with session_factory() as s:
        feed = CustomFeed(name="LaraJobs", url="https://larajobs.com/feed")
        s.add(feed)
        s.commit()
        key = f"feed:{feed.id}"
        only = Listing(name="A", keywords="php", location="X", sites=json.dumps([key]))
        mixed = Listing(name="B", keywords="php", location="X", sites=json.dumps(["hn", key]))
        default = Listing(name="C", keywords="php", location="X")
        s.add_all([only, mixed, default, Job(external_id=f"{key}:1", source="feed", title="T", company="Acme",
                                             location="", url="https://example.com/1")])
        s.commit()
        assert remove_site(s, key) == 1
        s.refresh(only), s.refresh(mixed), s.refresh(default)
        assert only.sites is None and only.search_changed_at is not None
        assert json.loads(mixed.sites) == ["hn"] and default.sites is None
        assert s.exec(select(CustomFeed)).all() == [] and len(s.exec(select(Job)).all()) == 1


def test_removing_a_feed_drops_its_stored_error_and_its_id_is_not_reused(session_factory):
    with session_factory() as s:
        feed = CustomFeed(name="LaraJobs", url="https://larajobs.com/feed")
        s.add(feed)
        s.commit()
        old_id = feed.id
        save_feed_errors(s, {f"feed:{old_id}": "down", "hn": "slow"}, utcnow())
        since = load_feed_errors(s)["hn"]["since"]
        remove_site(s, f"feed:{old_id}")
        assert load_feed_errors(s) == {"hn": {"error": "slow", "since": since}}
        again = CustomFeed(name="Acme jobs", url="https://example.com/feed")
        s.add(again)
        s.commit()
        assert again.id != old_id  # a new feed never inherits the old one's key, nor its error


def test_labels_for_job_sources():
    assert list(BUILTIN) == ["linkedin", "remoteok", "himalayas", "hn", "devbg", "infostud", "4dayweek"]
    assert SOURCE_LABELS["devbg"] == "DEV.BG" and SOURCE_LABELS["infostud"] == "HelloWorld/Infostud"
    assert SOURCE_LABELS["4dayweek"] == "4 Day Week" and SOURCE_LABELS["feed"] == "Your feeds"
    assert SOURCE_LABELS["lever"] == "Lever"


def test_remove_site_ignores_keys_that_are_not_own_sites(session_factory):
    with session_factory() as s:
        feed = CustomFeed(name="LaraJobs", url="https://larajobs.com/feed")
        board = WatchedCompany(company_key="acme", name="Acme", ats="lever", token="acme")
        s.add_all([feed, board])
        s.commit()
        stored = json.dumps(["hn", "devbg"])
        s.add(Listing(name="A", keywords="php", location="X", sites=stored))
        s.commit()
        for key in ("hn", f"devbg:{board.id}", f"linkedin:{feed.id}", f"hn:{board.id}", "nonsense"):
            assert remove_site(s, key) == 0
        assert len(s.exec(select(CustomFeed)).all()) == 1 and len(s.exec(select(WatchedCompany)).all()) == 1
        assert s.exec(select(Listing)).one().sites == stored
