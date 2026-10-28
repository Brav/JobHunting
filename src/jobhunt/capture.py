"""Jobs from any site: the schema.org JobPosting that most job pages carry as JSON-LD, read into the job's fields."""

import html
import json
import re
from datetime import date

from markdownify import markdownify

from jobhunt.places import place_name
from jobhunt.sources.feeds import USER_AGENT, pay_text

_JSONLD = re.compile(r"<script[^>]*type\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>", re.I | re.S)
# Greenhouse's job boards carry no JSON-LD; its public API has the same job.
_GREENHOUSE = re.compile(r"https?://(?:job-)?boards(\.eu)?\.greenhouse\.io/([\w-]+)/jobs/(\d+)")
_PERIODS = {"YEAR": "a year", "MONTH": "a month", "WEEK": "a week", "DAY": "a day", "HOUR": "an hour"}


def extract_jsonld(html: str) -> list[str]:
    return [m.group(1).strip() for m in _JSONLD.finditer(html or "")]


def _postings(node):
    """Every JobPosting in a JSON-LD value: a single object, a list, or an @graph."""
    if isinstance(node, list):
        for item in node:
            yield from _postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        yield from _postings(node.get("@graph"))


def _name(value) -> str:
    if isinstance(value, dict):
        value = value.get("name") or ""
    value = str(value or "").strip()
    return place_name(value) if len(value) == 2 and value.isupper() else value


def _location(posting: dict) -> tuple[str, bool]:
    remote = "TELECOMMUTE" in str(posting.get("jobLocationType", "")).upper()
    if remote:
        required = posting.get("applicantLocationRequirements") or []
        names = [_name(r) for r in (required if isinstance(required, list) else [required])]
        names = [n for n in names if n]
        return ("Remote: " + ", ".join(names)) if names else "Remote", True
    places = posting.get("jobLocation") or []
    for place in places if isinstance(places, list) else [places]:
        address = place.get("address") if isinstance(place, dict) else None
        if isinstance(address, dict):
            parts = [_name(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
            parts = [p for p in dict.fromkeys(parts) if p]
            if parts:
                return ", ".join(parts), False
        elif isinstance(address, str) and address.strip():
            return address.strip(), False
    return "", False


def _salary(posting: dict) -> str:
    pay = posting.get("baseSalary")
    if not isinstance(pay, dict):
        return ""
    value = pay.get("value")
    currency = str(pay.get("currency") or "").upper()
    if isinstance(value, dict):
        low, high = value.get("minValue") or value.get("value"), value.get("maxValue")
        unit = str(value.get("unitText") or "YEAR").upper()
    else:
        low, high, unit = value, None, "YEAR"
    try:
        return (pay_text(float(low or 0), float(high or 0), currency, _PERIODS.get(unit, "a year")) or "") if currency else ""
    except (TypeError, ValueError):
        return ""


def job_from_jsonld(blocks: list[str], page_url: str) -> dict | None:
    """The first JobPosting in the page's JSON-LD blocks as form fields, or None when there is none."""
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for posting in _postings(data):
            location, remote = _location(posting)
            posted = str(posting.get("datePosted") or "")[:10]
            try:
                posted = date.fromisoformat(posted).isoformat()
            except ValueError:
                posted = ""
            description = re.sub(r"\n{3,}", "\n\n", markdownify(str(posting.get("description") or ""))).strip()
            return {"title": " ".join(str(posting.get("title") or "").split()),
                    "company": _name(posting.get("hiringOrganization")),
                    "location": location, "remote": remote, "url": page_url,
                    "apply_url": str(posting.get("url") or "").strip(),
                    "description": description, "posted_at": posted, "salary_text": _salary(posting)}
    return None


def fetch_and_parse(url: str, get) -> tuple[dict | None, str | None]:
    """Download the page and read its JobPosting; (fields, None) or (None, why not)."""
    if not re.match(r"https?://", url):
        return None, "Only web addresses (http or https) can be fetched."
    greenhouse = _GREENHOUSE.match(url)
    if greenhouse:
        return _from_greenhouse(url, *greenhouse.groups(), get)
    try:
        response = get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    except Exception as e:  # requests raises many kinds; the user only needs to know it failed
        return None, f"Couldn't fetch the page ({type(e).__name__})."
    if response.status_code != 200:
        return None, f"The site answered {response.status_code} — fill the job in by hand."
    job = job_from_jsonld(extract_jsonld(response.text), url)
    return (job, None) if job else (None, "No job details on that page — fill them in by hand.")


def _from_greenhouse(url: str, eu: str | None, board: str, job_id: str, get) -> tuple[dict | None, str | None]:
    api = f"https://boards-api{eu or ''}.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
    try:
        response = get(api, headers={"User-Agent": USER_AGENT}, timeout=20)
        data = response.json() if response.status_code == 200 else None
    except Exception as e:  # network errors and a body that isn't JSON alike
        return None, f"Couldn't fetch the job from Greenhouse ({type(e).__name__})."
    if not data:
        return None, f"Greenhouse answered {response.status_code} — the job may be closed; fill it in by hand."
    location = (data.get("location") or {}).get("name") or ""
    description = re.sub(r"\n{3,}", "\n\n", markdownify(html.unescape(data.get("content") or ""))).strip()
    return {"title": " ".join(str(data.get("title") or "").split()), "company": data.get("company_name") or board,
            "location": location, "remote": "remote" in location.casefold(), "url": url,
            "apply_url": data.get("absolute_url") or "", "description": description,
            "posted_at": str(data.get("first_published") or data.get("updated_at") or "")[:10], "salary_text": ""}, None
