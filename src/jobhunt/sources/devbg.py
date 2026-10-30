"""DEV.BG, a Bulgarian tech job board, read through its WordPress REST API."""

import html
import re
from datetime import date

from jobhunt.sources.base import JobPosting
from jobhunt.sources.feeds import FeedPosting, _get_json, _markdown, search_words

CITIES = {"sofiya": "Sofia", "plovdiv": "Plovdiv", "varna": "Varna", "burgas": "Burgas", "ruse": "Ruse"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", html.unescape(text).casefold()).strip("-")


def company_from_link(link: str, title: str) -> str:
    """DEV.BG links read "<company>-<title>": the company is whatever comes before the title's slug."""
    last = link.rstrip("/").rsplit("/", 1)[-1]
    slug = _slug(title)
    if not slug or not last.endswith("-" + slug):
        return ""
    return " ".join(w.capitalize() for w in last[:-len(slug) - 1].split("-") if w)


def _location(tags: set[str]) -> tuple[str, bool | None]:
    if "remote" in tags:
        return "Remote, Bulgaria", True
    city = next((name for tag, name in CITIES.items() if tag in tags), "")
    return (f"{city}, Bulgaria" if city else "Bulgaria") + (" (hybrid)" if "hybrid" in tags else ""), None


def devbg(keywords: str, get) -> list[FeedPosting]:
    words = search_words(keywords)
    if not words:
        return []
    found = []
    for item in _get_json(get, "https://dev.bg/wp-json/wp/v2/job-listings", search=words, per_page=100,
                          _fields="id,title,link,date_gmt,content,class_list"):
        classes = item.get("class_list") or []
        tags = {c.removeprefix("job_listings_tag-") for c in classes if c.startswith("job_listings_tag-")}
        location, remote = _location(tags)
        title = html.unescape(item["title"]["rendered"]).strip()
        link = item.get("link") or ""
        posted = (item.get("date_gmt") or "")[:10]
        found.append(FeedPosting(JobPosting(
            external_id=f"devbg:{item['id']}", title=title, company=company_from_link(link, title),
            location=location, url=link, posted_at=date.fromisoformat(posted) if posted else None, is_remote=remote,
            description=_markdown((item.get("content") or {}).get("rendered")), source="devbg"),
            tuple(c.split("-", 1)[1].replace("-", " ") for c in classes
                  if c.startswith(("techstack-", "job_listing_category-")))))
    return found
