"""Job feeds besides LinkedIn: Remote OK, Himalayas and Hacker News "Who is hiring?", read through their public APIs.

`jobhunt.sources.combined.CombinedSource` runs them for each listing's chosen sites.
"""

import html
import logging
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

from markdownify import markdownify

from jobhunt import places as place_names
from jobhunt.sources.base import JobPosting

logger = logging.getLogger(__name__)

USER_AGENT = "jobhunt (personal job tracker; one request per run)"
MAX_AGE_DAYS = 30  # older postings are left out
LEDE_CHARS = 400  # how much of a description counts when matching a listing's keywords
_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


@dataclass(frozen=True)
class FeedPosting:
    posting: JobPosting
    tags: tuple[str, ...] = ()


def _get_json(get, url: str, **params):
    response = get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    return response.json()


def _get_bytes(get, url: str) -> bytes:
    response = get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    return response.content


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


# Places the places module doesn't know, mostly local spellings: a listing's place words say where the owner looks,
# not what the job is, so a feed job that never names the place still counts.
LOCAL_PLACE_NAMES = frozenset({
    "srbija", "србија", "beograd", "београд", "niš", "nis", "novi sad", "kragujevac",
    "българия", "sofia", "sofiya", "софия", "plovdiv", "пловдив", "varna", "варна", "burgas", "бургас"})


def is_place_word(word: str) -> bool:
    """A country, city, region or local place name, and nothing else (`EU` counts, `us` doesn't)."""
    word = word.strip().strip('"').strip()
    if word.casefold() in LOCAL_PLACE_NAMES:
        return True
    if word.upper() in place_names.ABBREVIATIONS and word not in place_names.ABBREVIATIONS:
        return False  # short forms only count in capitals: "US", not the pronoun "us"
    if not place_names.places_in(word):
        return False
    for pattern in (place_names._COUNTRY, place_names._ABBREVIATION, place_names._CITY, place_names._REGION):
        word = pattern.sub(" ", word)
    return not re.sub(r"[\s\W_]+", "", word)


def search_words(keywords: str) -> str:
    """The keywords a search API is sent: no place words, "remote" or operators."""
    words = [w for w in re.findall(r'"[^"]+"|\S+', keywords)
             if w.lower() not in ("remote", "and", "or", "not") and not is_place_word(w)]
    return " ".join(w.strip('"') for w in words)


def himalayas(keywords: str, get) -> list[FeedPosting]:
    data = _get_json(get, "https://himalayas.app/jobs/api/search", q=search_words(keywords), limit=20)
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
        if negate:
            unwanted.append(_normalise(word))
        elif not is_place_word(token):  # where the owner looks, not what the job is
            wanted.append(_normalise(word))
        negate = False

    def has(word):
        return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None
    return bool(wanted) and all(has(w) for w in wanted) and not any(has(w) for w in unwanted)
