"""The search every listing runs: LinkedIn plus the other sites the listing chose.

`CombinedSource` wraps the LinkedIn search. A listing's `sites` say which others it reads: built-in feeds, the user's
own feeds and their watched companies' boards. Each site is fetched once per run (cached for an hour); a site that
fails is logged and skipped, never failing the listing.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from jobhunt import site_check, watchlist
from jobhunt.sites import BUILTIN, OwnSite
from jobhunt.sources import rss
from jobhunt.sources.base import JobPosting, SourceError
from jobhunt.sources.devbg import devbg
from jobhunt.sources.feeds import (MAX_AGE_DAYS, FeedPosting, himalayas, hn_whos_hiring, matches_keywords, remoteok,
                                   search_words)
from jobhunt.sources.fourdayweek import four_day_week
from jobhunt.sources.infostud import infostud
from jobhunt.timeutil import utcnow

logger = logging.getLogger(__name__)

# Each built-in site except LinkedIn: (keywords, get, home country) -> its postings.
FETCHERS: dict[str, Callable[[str, Callable, str], list[FeedPosting]]] = {
    "remoteok": lambda keywords, get, home: remoteok(get),
    "himalayas": lambda keywords, get, home: himalayas(keywords, get),
    "hn": lambda keywords, get, home: hn_whos_hiring(get),
    "devbg": lambda keywords, get, home: devbg(keywords, get),
    "infostud": lambda keywords, get, home: infostud(get),
    "4dayweek": lambda keywords, get, home: four_day_week(keywords, home, get),
}


class CombinedSource:
    """The LinkedIn search plus the jobs of the listing's other sites matching the same keywords."""

    def __init__(self, primary, *, get, own: Callable[[], list[OwnSite]] = lambda: [],
                 home_country: Callable[[], str] = lambda: "", clock: Callable[[], datetime] = utcnow,
                 ttl: timedelta = timedelta(hours=1), robots_ttl: timedelta = timedelta(days=1)):
        self.primary, self.get, self.own, self.home_country = primary, get, own, home_country
        self.clock, self.ttl, self.robots_ttl = clock, ttl, robots_ttl
        self._cache: dict[tuple, tuple[datetime, list[FeedPosting]]] = {}
        self._robots: dict[str, tuple[datetime, RobotFileParser]] = {}  # per host; only successful reads
        self.errors: dict[str, str] = {}  # site key -> what went wrong
        self.on_errors: Callable[[dict[str, str]], None] | None = None  # told whenever the failing sites change

    def _check_robots(self, url: str) -> None:
        host = urlsplit(url).netloc
        hit = self._robots.get(host)
        if hit and self.clock() - hit[0] < self.robots_ttl:
            rules = hit[1]
        else:
            try:
                rules = site_check.robots_rules(url, self.get)
            except site_check.Refused:
                raise SourceError("couldn't read its robots.txt") from None
            self._robots[host] = (self.clock(), rules)
        if not rules.can_fetch("jobhunt", url):
            raise SourceError("robots.txt now disallows it")

    def _site(self, key: str, keywords: str, own: dict[str, OwnSite]) -> list[FeedPosting]:
        site = own.get(key)
        if site:
            cache_key = (key, site.url, site.token)
        elif BUILTIN[key].per_keywords:
            cache_key = (key, search_words(keywords))
        else:
            cache_key = (key,)
        hit = self._cache.get(cache_key)
        if hit and self.clock() - hit[0] < self.ttl:
            return hit[1]
        if site and site.kind == "feed":
            self._check_robots(site.url)
            fetched = rss.fetch_feed(int(key.partition(":")[2]), site.url, self.get)
        elif site:
            fetched = watchlist.fetch_board(site.kind, site.token, site.name, self.get)
        else:
            fetched = FETCHERS[key](keywords, self.get, self.home_country())
        self._cache[cache_key] = (self.clock(), fetched)
        return fetched

    def search(self, keywords: str, location: str, radius_km: int, window_hours: int, limit: int,
               sites: tuple[str, ...] | None = None) -> list[JobPosting]:
        sites = sites if sites is not None else ("linkedin",)
        own = {o.key: o for o in self.own()}
        postings = list(self.primary.search(keywords, location, radius_km, window_hours, limit)) \
            if "linkedin" in sites else []
        before = dict(self.errors)
        for key in [k for k in self.errors if k.partition(":")[0] in ("feed", "board") and k not in own]:
            del self.errors[key]  # an own site that was removed
        oldest = (self.clock() - timedelta(days=MAX_AGE_DAYS)).date()
        for key in sites:
            if key == "linkedin" or (key not in own and key not in FETCHERS):
                continue
            if key not in own and BUILTIN[key].per_keywords and not search_words(keywords):
                continue  # nothing to ask it
            try:
                feed = self._site(key, keywords, own)
                self.errors.pop(key, None)
            except Exception as e:  # a site's trouble never fails the listing
                logger.warning("%s failed: %s", key, e)
                self.errors[key] = str(e)[:200]
                continue
            postings += [f.posting for f in feed if matches_keywords(f, keywords)
                         and (f.posting.posted_at is None or f.posting.posted_at >= oldest)]
        if self.errors != before and self.on_errors:
            try:
                self.on_errors(dict(self.errors))
            except Exception:
                logger.exception("couldn't save the site errors")
        return postings
