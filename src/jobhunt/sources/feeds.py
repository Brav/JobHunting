"""Job feeds besides LinkedIn: Remote OK, Himalayas and Hacker News "Who is hiring?", read through their public APIs.

`CombinedSource` wraps the LinkedIn search: each listing's search also returns the feed jobs matching its keywords.
Each feed is fetched once per run (cached for an hour); a feed that fails is logged and skipped, never failing a run.
"""

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from markdownify import markdownify

from jobhunt.sources.base import JobPosting
from jobhunt.timeutil import utcnow

logger = logging.getLogger(__name__)

USER_AGENT = "jobhunt (personal job tracker; one request per run)"
MAX_AGE_DAYS = 30  # older postings are left out
LEDE_CHARS = 400  # how much of a description counts when matching a listing's keywords
SOURCE_LABELS = {"linkedin": "LinkedIn", "remoteok": "Remote OK", "himalayas": "Himalayas", "hn": "Hacker News",
                 "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "capture": "the original site"}
_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


@dataclass(frozen=True)
class FeedPosting:
    posting: JobPosting
    tags: tuple[str, ...] = ()


def _get_json(get, url: str, **params):
    response = get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    return response.json()


def _markdown(text: str | None) -> str:
    return re.sub(r"\n{3,}", "\n\n", markdownify(text or "")).strip()


def pay_text(low, high, currency: str, period: str = "a year") -> str | None:
    low, high = int(low or 0), int(high or 0)
    if not high and not low:
        return None
    sign = _SYMBOL.get(currency, f"{currency} ")
    amounts = f"{sign}{low:,}–{sign}{high:,}" if low and high and low != high else f"{sign}{(high or low):,}"
    return f"{amounts} {period}"


def remoteok(get) -> list[FeedPosting]:
    found = []
    for item in _get_json(get, "https://remoteok.com/api"):
        if "id" not in item or not item.get("position"):
            continue  # the first entry is the API's legal notice
        tags = tuple(item.get("tags") or ())
        posted = item.get("date", "")[:10]
        found.append(FeedPosting(JobPosting(
            external_id=f"remoteok:{item['id']}", title=item["position"].strip(), company=(item.get("company") or "").strip(),
            location=(item.get("location") or "").strip() or "Remote", url=item.get("url") or "",
            posted_at=date.fromisoformat(posted) if posted else None, is_remote=True,
            description=_markdown(item.get("description")), source="remoteok",
            salary_text=pay_text(item.get("salary_min"), item.get("salary_max"), "USD"),
            apply_url=item.get("apply_url")), tags))
    return found


def _search_words(keywords: str) -> str:
    words = [w for w in re.findall(r'"[^"]+"|\S+', keywords) if w.lower() not in ("remote", "and", "or", "not")]
    return " ".join(w.strip('"') for w in words)


def himalayas(keywords: str, get) -> list[FeedPosting]:
    data = _get_json(get, "https://himalayas.app/jobs/api/search", q=_search_words(keywords), limit=20)
    found = []
    for item in data.get("jobs", []):
        places = [p for p in item.get("locationRestrictions") or [] if p]
        if places:
            location = "Remote: " + ", ".join(places[:3]) + (f" +{len(places) - 3} more" if len(places) > 3 else "")
            where = f"We hire in {', '.join(places)}."
        else:
            location, where = "Remote, worldwide", "Remote worldwide."
        published = item.get("pubDate")
        posted = datetime.fromtimestamp(int(published), timezone.utc).date() if str(published or "").isdigit() else None
        period = {"annual": "a year", "monthly": "a month", "hourly": "an hour"}.get(item.get("salaryPeriod"), "a year")
        found.append(FeedPosting(JobPosting(
            external_id=f"himalayas:{item.get('guid') or item.get('applicationLink')}", title=item["title"].strip(),
            company=(item.get("companyName") or "").strip(), location=location,
            url=item.get("applicationLink") or item.get("guid") or "", posted_at=posted, is_remote=True,
            description=f"{where}\n\n{_markdown(item.get('description'))}", source="himalayas",
            salary_text=pay_text(item.get("minSalary"), item.get("maxSalary"), item.get("currency") or "USD", period),
            apply_url=item.get("applicationLink")), tuple(item.get("categories") or ())))
    return found


_ROLE = re.compile(r"engineer|developer|programmer|lead|architect|cto|devops|sre|scientist|designer|manager|head of",
                   re.IGNORECASE)
_WHERE = re.compile(r"remote|onsite|on-site|hybrid|\b(?:EU|US|UK|EMEA|Europe)\b", re.IGNORECASE)


def _hn_posting(comment: dict) -> FeedPosting | None:
    raw = comment.get("text")
    if not raw:
        return None
    first = html.unescape(re.split(r"<p>", raw, maxsplit=1)[0])
    first = re.sub(r"<[^>]+>", "", first).strip()
    parts = [p.strip() for p in re.split(r"\s+[|—–]\s+|\s\|\s|\s+-\s+", first) if p.strip()]
    if len(parts) < 2:
        return None
    company, location = parts[0], ""
    inner = re.match(r"^(.*?)\s*\((.+)\)$", company)
    if inner:
        company, location = inner.group(1).strip(), inner.group(2).strip()
    title = next((p for p in parts[1:] if _ROLE.search(p)), parts[1])
    location = location or next((p for p in parts[1:] if p is not title and _WHERE.search(p)), "")
    posted = (comment.get("created_at") or "")[:10]
    return FeedPosting(JobPosting(
        external_id=f"hn:{comment['id']}", title=title, company=company, location=location,
        url=f"https://news.ycombinator.com/item?id={comment['id']}",
        posted_at=date.fromisoformat(posted) if posted else None,
        is_remote=bool(re.search(r"remote", first, re.IGNORECASE)), description=_markdown(raw), source="hn"))


def hn_whos_hiring(get) -> list[FeedPosting]:
    """The newest "Ask HN: Who is hiring?" thread's job comments."""
    hits = _get_json(get, "https://hn.algolia.com/api/v1/search_by_date", tags="story,author_whoishiring",
                     query="who is hiring", hitsPerPage=5).get("hits", [])
    thread = next((h for h in hits if h.get("title", "").startswith("Ask HN: Who is hiring?")), None)
    if thread is None:
        return []
    item = _get_json(get, f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
    return [p for p in (_hn_posting(c) for c in item.get("children", [])) if p]


_SYNONYMS = {"engineer": "developer", "programmer": "developer", "dev": "developer"}


def _normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"full[\s-]stack", "fullstack", text)
    text = re.sub(r"front[\s-]end", "frontend", text)
    text = re.sub(r"back[\s-]end", "backend", text)
    return re.sub(r"\b(" + "|".join(_SYNONYMS) + r")\b", lambda m: _SYNONYMS[m.group(1)], text)


def matches_keywords(feed: FeedPosting, keywords: str) -> bool:
    """Every word of the listing's keywords (but "remote" and operators) in the job; NOT excludes the next word."""
    # The title, tags and the opening of the description: a stack named deep in a company blurb isn't the job.
    lede = re.sub(r"https?://\S+|\S+\.php\b", " ", (feed.posting.description or "")[:LEDE_CHARS])
    text = _normalise(" ".join([feed.posting.title, " ".join(feed.tags), lede]))
    tokens = re.findall(r'"[^"]+"|\S+', keywords)
    wanted, unwanted, negate = [], [], False
    for token in tokens:
        word = token.strip('"').lower()
        if word == "not":
            negate = True
            continue
        if word in ("remote", "and", "or"):
            continue
        (unwanted if negate else wanted).append(_normalise(word))
        negate = False

    def has(word):
        return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None
    return bool(wanted) and all(has(w) for w in wanted) and not any(has(w) for w in unwanted)


FETCHERS: dict[str, Callable] = {"remoteok": lambda keywords, get: remoteok(get),
                                 "himalayas": himalayas,
                                 "hn": lambda keywords, get: hn_whos_hiring(get)}
_PER_KEYWORDS = {"himalayas"}  # searched per listing; the others are one feed for every listing


class CombinedSource:
    """The LinkedIn search plus the enabled feeds' jobs matching the same keywords."""

    def __init__(self, primary, *, get, enabled: Callable[[], tuple[str, ...]],
                 watched: Callable[[], list[tuple[str, str, str]]] = lambda: [],
                 clock: Callable[[], datetime] = utcnow, ttl: timedelta = timedelta(hours=1)):
        self.primary, self.get, self.enabled, self.clock, self.ttl = primary, get, enabled, clock, ttl
        self.watched = watched  # (ats, board token, company name) of each watched company
        self._cache: dict[tuple, tuple[datetime, list[FeedPosting]]] = {}
        self.errors: dict[str, str] = {}
        self.on_errors: Callable[[dict[str, str]], None] | None = None  # told whenever the failing feeds change

    def _feed(self, name: str, keywords: str) -> list[FeedPosting]:
        key = (name, keywords) if name in _PER_KEYWORDS else (name,)
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < self.ttl:
            return hit[1]
        if ":" in name:  # a watched company's board: "greenhouse:acme:Acme"
            from jobhunt.watchlist import fetch_board
            ats, token, company = name.split(":", 2)
            fetched = fetch_board(ats, token, company, self.get)
        else:
            fetched = FETCHERS[name](keywords, self.get)
        self._cache[key] = (self.clock(), fetched)
        return fetched

    def search(self, keywords: str, location: str, radius_km: int, window_hours: int, limit: int) -> list[JobPosting]:
        postings = list(self.primary.search(keywords, location, radius_km, window_hours, limit))
        oldest = (self.clock() - timedelta(days=MAX_AGE_DAYS)).date()
        boards = [f"{ats}:{token}:{company}" for ats, token, company in self.watched()]
        before = dict(self.errors)
        for name in [*self.enabled(), *boards]:
            if name not in FETCHERS and ":" not in name:
                continue
            try:
                feed = self._feed(name, keywords)
                self.errors.pop(name, None)
            except Exception as e:  # a feed's trouble never fails the LinkedIn search
                logger.warning("%s feed failed: %s", name, e)
                self.errors[name] = str(e)[:200]
                continue
            postings += [f.posting for f in feed if matches_keywords(f, keywords)
                         and (f.posting.posted_at is None or f.posting.posted_at >= oldest)]
        if self.errors != before and self.on_errors:
            try:
                self.on_errors(dict(self.errors))
            except Exception:
                logger.exception("couldn't save the feed errors")
        return postings
