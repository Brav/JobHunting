"""The checks before an own site is added: address, terms, robots.txt, one test fetch, recognition and jobs."""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from jobhunt import watchlist
from jobhunt.sources import rss
from jobhunt.sources.base import JobPosting, SourceError
from jobhunt.sources.feeds import USER_AGENT

ADDRESS = "Only http and https addresses can be added."
PRIVATE = "That address is on this computer or a private network."
NO_HOST = "Couldn't find that site."
TERMS = "This site's terms forbid automated reading."
ROBOTS_DISALLOWED = "Its robots.txt doesn't allow reading this address."
ROBOTS_UNREADABLE = "Couldn't read its robots.txt."
BLOCKED = "This site blocks automated reading."
STATUS = "The site answered {status}."
UNREACHABLE = "Couldn't reach the site: {error}"
NOTHING = "No feed or known hiring system found at this address."
NO_JOBS = "No jobs found there."
BOARD_FAILED = "Couldn't read its {system} jobs: {error}"

# Sites whose terms forbid automated reading. A host matches when it is, or is under, one of these.
_FORBIDDEN_HOSTS = ("jobs.bg", "karieri.bg", "joberty.com", "joberty.rs", "remotive.com", "wellfound.com",
                    "welcometothejungle.com", "jobgether.com", "vuejobs.com", "linkedin.com")
_FORBIDDEN_LABELS = ("indeed", "glassdoor")
_BLOCKED_STATUSES = (401, 403, 429)
_CHALLENGES = ("Just a moment", "cf-challenge", "cf_chl")
_PREVIEW = 5


class Refused(Exception):
    """The site may not be added; str(e) is the reason shown."""


@dataclass(frozen=True)
class Candidate:
    kind: str  # "feed" or a hiring-system key
    url: str  # the feed address, or the careers page as pasted
    name: str
    token: str = ""
    jobs: tuple[JobPosting, ...] = ()


def normalise_url(url: str) -> str:
    url = url.strip()
    return url if "://" in url else "https://" + url


def _check_address(url: str, resolve) -> None:
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise Refused(ADDRESS)
    try:
        found = resolve(host, None)
    except socket.gaierror:
        raise Refused(NO_HOST) from None
    if not found or not all(ipaddress.ip_address(item[4][0]).is_global for item in found):
        raise Refused(PRIVATE)


def _check_terms(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if (any(host == d or host.endswith("." + d) for d in _FORBIDDEN_HOSTS)
            or any(label in _FORBIDDEN_LABELS for label in host.split("."))):
        raise Refused(TERMS)


def robots_rules(url: str, get) -> RobotFileParser:
    """The site's robots.txt rules. A 4xx answer means everything is allowed (RFC 9309)."""
    parts = urlsplit(url)
    address = f"{parts.scheme}://{parts.netloc}/robots.txt"
    rules = RobotFileParser(address)
    try:
        response = get(address, headers={"User-Agent": USER_AGENT}, timeout=15)
    except OSError:
        raise Refused(ROBOTS_UNREADABLE) from None
    if response.status_code >= 500:
        raise Refused(ROBOTS_UNREADABLE)
    if 400 <= response.status_code < 500:
        rules.allow_all = True
    else:
        rules.parse(response.text.splitlines())
        rules.modified()
    return rules


def _check_saving(url: str, get, resolve) -> None:
    """Checks 1-3 on an address that is already normalised."""
    _check_address(url, resolve)
    _check_terms(url)
    if not robots_rules(url, get).can_fetch("jobhunt", url):
        raise Refused(ROBOTS_DISALLOWED)


def check_saving(url: str, get, resolve) -> None:
    _check_saving(normalise_url(url), get, resolve)


def _test_fetch(url: str, get, resolve):
    try:
        response = get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    except OSError as error:
        raise Refused(UNREACHABLE.format(error=error)) from None
    if response.url and response.url != url:  # a redirect must not lead somewhere we would have refused
        _check_address(response.url, resolve)
        _check_terms(response.url)
    if response.status_code in _BLOCKED_STATUSES or any(marker in response.text[:20000] for marker in _CHALLENGES):
        raise Refused(BLOCKED)
    if response.status_code != 200:
        raise Refused(STATUS.format(status=response.status_code))
    return response


def _page_title(html: str) -> str:
    title = BeautifulSoup(html, "html.parser").find("title")
    return title.get_text().strip() if title else ""


def _token_name(token: str) -> str:
    return " ".join(token.replace("-", " ").replace("_", " ").split()).title()


def _feed_candidate(feed_url: str, name: str, content: bytes) -> Candidate:
    jobs = tuple(item.posting for item in rss.feed_postings(0, content)[:_PREVIEW])
    if not jobs:
        raise Refused(NO_JOBS)
    return Candidate("feed", feed_url, name or rss.feed_title(content) or urlsplit(feed_url).hostname or "", jobs=jobs)


def _board_candidate(board: tuple[str, str], url: str, name: str, get) -> Candidate:
    ats, token = board
    name = name or _token_name(token)  # a page title, when there is one, was passed in as the name
    try:
        found = watchlist.fetch_board(ats, token, name, get)
    except (OSError, ValueError, LookupError, TypeError, SourceError) as error:
        raise Refused(BOARD_FAILED.format(system=watchlist.ATS[ats], error=error)) from None
    jobs = tuple(item.posting for item in found[:_PREVIEW])
    if not jobs:
        raise Refused(NO_JOBS)
    return Candidate(ats, url, name, token, jobs)


def check_site(url: str, name: str, get, resolve) -> Candidate:
    """All six checks. Raises Refused with the reason shown to the owner."""
    url, name = normalise_url(url), name.strip()
    _check_saving(url, get, resolve)
    response = _test_fetch(url, get, resolve)
    if board := watchlist.board_from_url(url):
        return _board_candidate(board, url, name, get)
    if rss.is_feed(response.content):
        return _feed_candidate(url, name, response.content)
    if advertised := rss.advertised_feed(response.text, url):
        _check_saving(advertised, get, resolve)
        feed = _test_fetch(advertised, get, resolve)
        if rss.is_feed(feed.content):
            return _feed_candidate(advertised, name, feed.content)
    if board := watchlist.board_in_page(response.text):
        return _board_candidate(board, url, name or _page_title(response.text), get)
    raise Refused(NOTHING)
