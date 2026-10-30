"""A generic RSS/Atom job-feed reader. feedparser only ever parses bytes we fetched ourselves, never a URL."""

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup

from jobhunt.sources.base import JobPosting, SourceError
from jobhunt.sources.feeds import FeedPosting, _get_bytes, _markdown

# A DOCTYPE with an internal subset can declare entities; we never want them read.
_DOCTYPE = re.compile(rb"<!DOCTYPE[^>\[]*(\[.*?\])?\s*>", re.IGNORECASE | re.DOTALL)
_TRAILING_PLACE = re.compile(r"\s*\(([^()]+)\)\s*$")
_FEED_TYPES = ("application/rss+xml", "application/atom+xml")


def parse(content: bytes):
    result = feedparser.parse(_DOCTYPE.sub(b"", content))
    if not result.version:
        raise SourceError("Not an RSS or Atom feed")
    return result


def is_feed(content: bytes) -> bool:
    try:
        parse(content)
    except SourceError:
        return False
    return True


def _written_date(text: str) -> date | None:
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def entry_date(entry) -> date | None:
    """The date as the feed wrote it, in the feed's own offset: a midnight +0100 date stays on its day."""
    for field in ("published", "updated"):
        if entry.get(field) and (found := _written_date(entry[field])):
            return found
    for field in ("published_parsed", "updated_parsed"):
        if parsed := entry.get(field):
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    return None


def split_title(title: str) -> tuple[str, str, str]:
    """(title, company, location) from "Title at Company", "Company: Title" or "Title – Company", each with an optional "(Location)"."""
    title = title.strip()
    place = _TRAILING_PLACE.search(title)
    head = title[:place.start()].strip() if place else title
    location = place.group(1).strip() if place else ""
    if " at " in head:
        job, company = head.rsplit(" at ", 1)
    elif ": " in head:
        company, job = head.split(": ", 1)
    elif match := re.search(r"\s[–—]\s", head):
        job, company = head[:match.start()], head[match.end():]
    else:
        return title, "", ""
    job, company = job.strip(), company.strip()
    if not job or not company:
        return title, "", ""
    return job, company, location


def _company_and_location(entry, title: str) -> tuple[str, str, str]:
    company = (entry.get("job_listing_company") or entry.get("job_company") or "").strip()
    location = (entry.get("job_listing_location") or entry.get("job_location") or entry.get("region") or "").strip()
    if not location and (entry.get("tt_city") or entry.get("tt_country")):
        location = ", ".join(p.strip() for p in (entry.get("tt_city"), entry.get("tt_country")) if p and p.strip())
    if company:
        return title, company, location
    job, split_company, split_location = split_title(title)
    return job, split_company, location or split_location


def entry_posting(entry, external_id: str, source: str) -> FeedPosting | None:
    title = (entry.get("title") or "").strip()
    link = (entry.get("link") or "").strip()
    if not title or not link:
        return None
    title, company, location = _company_and_location(entry, title)
    body = entry.get("summary") or (entry.get("content") or [{}])[0].get("value")
    tags = tuple(t.term for t in entry.get("tags") or () if t.get("term"))
    return FeedPosting(JobPosting(
        external_id=external_id, title=title, company=company, location=location, url=link,
        posted_at=entry_date(entry), description=_markdown(body), source=source), tags)


def feed_postings(feed_id: int, content: bytes) -> list[FeedPosting]:
    found = []
    for entry in parse(content).entries:
        key = entry.get("id") or entry.get("link") or ""
        if posting := entry_posting(entry, f"feed:{feed_id}:{key}", "feed"):
            found.append(posting)
    return found


def fetch_feed(feed_id: int, url: str, get) -> list[FeedPosting]:
    return feed_postings(feed_id, _get_bytes(get, url))


def feed_title(content: bytes) -> str:
    return (parse(content).feed.get("title") or "").strip()


def advertised_feed(html: str, base_url: str) -> str | None:
    """The first RSS or Atom feed a page advertises in its head, as an absolute address."""
    for link in BeautifulSoup(html, "html.parser").find_all("link", href=True):
        rel = link.get("rel") or []
        rel = rel.split() if isinstance(rel, str) else rel
        if "alternate" in [r.lower() for r in rel] and (link.get("type") or "").lower() in _FEED_TYPES:
            try:
                return urljoin(base_url, link["href"])
            except ValueError:  # a malformed address, e.g. an unclosed "[": try the next link
                continue
    return None
