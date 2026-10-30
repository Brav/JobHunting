"""HelloWorld.rs and Infostud, Serbian job boards, read through their RSS feeds."""

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobhunt.sources import rss
from jobhunt.sources.base import JobPosting
from jobhunt.sources.feeds import FeedPosting, _get_bytes, _markdown

logger = logging.getLogger(__name__)

FEEDS = ("https://www.helloworld.rs/rss/", "https://rss.infostud.com/poslovi/rss.php")
_REMOTE = ("remote", "rad od kuće", "rad od kuce")


def clean_link(url: str) -> str:
    """The link without its utm_ tracking parameters."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def job_line(text: str) -> tuple[str, str, str]:
    """(company, city, mode) from "Company - City | Mode"; mode is "remote", "hybrid" or ""."""
    line, _, mode = text.strip().rpartition(" | ") if " | " in text else (text.strip(), "", "")
    mode = mode.strip().casefold()
    company, city = line.rsplit(" - ", 1) if " - " in line else (line, "")
    return company.strip(), city.strip(), "remote" if mode in _REMOTE else "hybrid" if mode == "hibrid" else ""


def _job_id(link: str) -> str | None:
    return next((p for p in reversed(urlsplit(link).path.split("/")) if p.isdigit()), None)


def _feed(url: str, get) -> list[FeedPosting]:
    found = []
    for entry in rss.parse(_get_bytes(get, url)).entries:
        title, link = (entry.get("title") or "").strip(), (entry.get("link") or "").strip()
        job = _job_id(link)
        if not title or not job:
            continue
        summary = entry.get("summary") or ""
        company, city, mode = job_line(summary)
        if mode == "remote":
            location = "Remote, Serbia"
        else:
            location = (f"{city}, Serbia" if city else "Serbia") + (" (hybrid)" if mode == "hybrid" else "")
        found.append(FeedPosting(JobPosting(
            external_id=f"infostud:{job}", title=title, company=company, location=location, url=clean_link(link),
            posted_at=rss.entry_date(entry), is_remote=True if mode == "remote" else None,
            description=_markdown(summary), source="infostud")))
    return found


def infostud(get) -> list[FeedPosting]:
    found, seen, errors = [], set(), []
    for url in FEEDS:
        try:
            feed = _feed(url, get)
        except Exception as e:  # one site being down must not hide the other's jobs
            logger.warning("%s failed: %s", url, e)
            errors.append(e)
            continue
        for item in feed:
            if item.posting.external_id not in seen:  # the same job is on both sites: the first one wins
                seen.add(item.posting.external_id)
                found.append(item)
    if len(errors) == len(FEEDS):
        raise errors[0]
    return found
