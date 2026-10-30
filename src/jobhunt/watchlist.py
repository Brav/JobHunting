"""Watched companies: new jobs read straight from their Greenhouse, Lever or Ashby job boards (public JSON)."""

import html
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

from jobhunt.sources import rss
from jobhunt.sources.base import JobPosting
from jobhunt.sources.feeds import FeedPosting, _get_bytes, _get_json, _markdown

ATS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workable": "Workable",
       "smartrecruiters": "SmartRecruiters", "recruitee": "Recruitee", "personio": "Personio",
       "teamtailor": "Teamtailor", "breezy": "Breezy"}
GUESSED = ("greenhouse", "lever", "ashby")  # the systems we try from a company's name alone
_URLS = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
         "lever": "https://api.lever.co/v0/postings/{token}",
         "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
         "workable": "https://apply.workable.com/api/v1/widget/accounts/{token}",
         "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
         "recruitee": "https://{token}.recruitee.com/api/offers/",
         "personio": "https://{token}.jobs.personio.com/search.json",
         "teamtailor": "https://{token}.teamtailor.com/jobs.rss",
         "breezy": "https://{token}.breezy.hr/json"}
_PARAMS = {"greenhouse": {"content": "true"}, "lever": {"mode": "json"}, "ashby": {"includeCompensation": "true"},
           "workable": {"details": "true"}, "smartrecruiters": {"limit": "100"}}

_PAGES = {"greenhouse": "https://job-boards.greenhouse.io/{t}", "lever": "https://jobs.lever.co/{t}",
          "ashby": "https://jobs.ashbyhq.com/{t}", "workable": "https://apply.workable.com/{t}",
          "smartrecruiters": "https://jobs.smartrecruiters.com/{t}", "recruitee": "https://{t}.recruitee.com",
          "personio": "https://{t}.jobs.personio.com", "teamtailor": "https://{t}.teamtailor.com/jobs",
          "breezy": "https://{t}.breezy.hr"}

# Careers-page hosts: the board name is the first path segment, or the leftmost label of the host.
_PATH_HOSTS = {"boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
               "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby", "apply.workable.com": "workable",
               "jobs.smartrecruiters.com": "smartrecruiters", "careers.smartrecruiters.com": "smartrecruiters"}
_SUBDOMAIN_HOSTS = {"recruitee.com": "recruitee", "jobs.personio.de": "personio", "jobs.personio.com": "personio",
                    "teamtailor.com": "teamtailor", "breezy.hr": "breezy"}
_NOT_BOARDS = {"www", "app", "api"}
_ADDRESS = re.compile(r"(?:https?:)?//[^\s\"'<>]+")


def board_page_url(ats: str, token: str) -> str:
    """The company's public careers page on its hiring system."""
    return _PAGES[ats].format(t=token)


def board_from_url(url: str) -> tuple[str, str] | None:
    """(system, board name) for a careers-page address on one of the nine hiring systems, else None."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().removesuffix(".")  # "jobs.lever.co." is jobs.lever.co
    except ValueError:  # not an address after all, e.g. "//[a-z]+" in a page's script
        return None
    segments = [x for x in parts.path.split("/") if x]
    if host in _PATH_HOSTS:
        ats = _PATH_HOSTS[host]
        if ats == "greenhouse" and segments[:1] == ["embed"]:
            token = (parse_qs(parts.query).get("for") or [""])[0]
        else:
            token = segments[0] if segments else ""
        return (ats, token) if token and not (ats == "workable" and token == "j") else None
    for suffix, ats in _SUBDOMAIN_HOSTS.items():
        label = host.removesuffix("." + suffix)
        if label != host and "." not in label and label not in _NOT_BOARDS:
            return ats, label
    return None


def board_in_page(html: str) -> tuple[str, str] | None:
    """The first careers-page address in a page's HTML that belongs to one of the nine hiring systems."""
    for found in _ADDRESS.findall(html):
        if board := board_from_url("https:" + found if found.startswith("//") else found):
            return board
    return None


def slug_candidates(name: str) -> list[str]:
    """Board names to try for a company: "Acme & Co" → acmeco, acme-co, acme."""
    words = re.findall(r"[a-z0-9]+", name.casefold())
    return list(dict.fromkeys(s for s in ("".join(words), "-".join(words), words[0] if words else "") if s))


def _day(text: str | None) -> date | None:
    """The date a timestamp starts with ("2026-10-22T08:00:00Z", "2026-10-22 08:00:00 UTC")."""
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _where(city, country, mode=None) -> str:
    """"City, Country (remote|hybrid)" from whatever parts are known."""
    place = ", ".join(p.strip() for p in (city, country) if p and p.strip())
    return f"{place} ({mode})".strip() if mode else place


def _posting(ats, token, company, job_id, title, location, url, posted, description, salary=None) -> FeedPosting:
    return FeedPosting(JobPosting(external_id=f"{ats}:{token}:{job_id}", title=title.strip(), company=company,
                                  location=location or "", url=url, posted_at=posted, description=description,
                                  source=ats, salary_text=salary, apply_url=url))


def _greenhouse(token, company, data):
    return [_posting("greenhouse", token, company, j["id"], j["title"], (j.get("location") or {}).get("name"),
                     j.get("absolute_url", ""), _day(j.get("first_published") or j.get("updated_at")),
                     _markdown(html.unescape(j.get("content") or ""))) for j in data.get("jobs", [])]


def _lever(token, company, data):
    found = []
    for j in data:
        where = (j.get("categories") or {}).get("location") or ""
        if j.get("workplaceType") in ("remote", "hybrid"):
            where = f"{where} ({j['workplaceType']})".strip()
        created = j.get("createdAt")
        found.append(_posting("lever", token, company, j["id"], j["text"], where, j.get("hostedUrl", ""),
                              datetime.fromtimestamp(created / 1000, timezone.utc).date() if created else None,
                              j.get("descriptionPlain") or _markdown(j.get("description"))))
    return found


def _ashby(token, company, data):
    return [_posting("ashby", token, company, j["id"], j["title"], j.get("location"), j.get("jobUrl", ""),
                     _day(j.get("publishedAt")), j.get("descriptionPlain") or _markdown(j.get("descriptionHtml")),
                     (j.get("compensation") or {}).get("compensationTierSummary")) for j in data.get("jobs", [])]


def _workable(token, company, data):
    return [_posting("workable", token, company, j["shortcode"], j["title"],
                     _where(j.get("city"), j.get("country"), "remote" if j.get("telecommuting") else None),
                     j.get("url", ""), _day(j.get("published_on")), _markdown(j.get("description")))
            for j in data.get("jobs", [])]


def _smartrecruiters(token, company, data):
    found = []
    for j in data.get("content", []):
        place = j.get("location") or {}
        mode = "remote" if place.get("remote") else "hybrid" if place.get("hybrid") else None
        full = place.get("fullLocation")
        where = _where(full, None, mode) if full else _where(place.get("city"), place.get("country"), mode)
        found.append(_posting("smartrecruiters", token, company, j["id"], j["name"], where,
                              f"https://jobs.smartrecruiters.com/{token}/{j['id']}", _day(j.get("releasedDate")), ""))
    return found


def _recruitee(token, company, data):
    found = []
    for j in data.get("offers", []):
        mode = "remote" if j.get("remote") else "hybrid" if j.get("hybrid") else None
        found.append(_posting("recruitee", token, company, j["id"], j["title"],
                              _where(j.get("city"), j.get("country"), mode), j.get("careers_url", ""),
                              _day(j.get("published_at")), _markdown(j.get("description"))))
    return found


def _personio(token, company, data):
    return [_posting("personio", token, company, j["id"], j["name"], j.get("office"),
                     f"https://{token}.jobs.personio.com/job/{j['id']}", None, "") for j in data]


def _breezy(token, company, data):
    found = []
    for j in data:
        place = j.get("location") or {}
        country = place.get("country")
        country = country.get("name") if isinstance(country, dict) else country
        found.append(_posting("breezy", token, company, j["id"], j["name"],
                              _where(place.get("city"), country, "remote" if place.get("is_remote") else None),
                              j.get("url", ""), _day(j.get("published_date")), _markdown(j.get("description"))))
    return found


def _teamtailor(token, company, content: bytes):
    found = []
    for entry in rss.parse(content).entries:
        key = entry.get("id") or entry.get("link") or ""
        if not (item := rss.entry_posting(entry, f"teamtailor:{token}:{key}", "teamtailor")):
            continue
        status = (entry.get("remotestatus") or "").strip().lower()
        mode = "remote" if status in ("fully", "temporary") else "hybrid" if status == "hybrid" else None
        where = _where(entry.get("tt_city"), entry.get("tt_country"), mode) or item.posting.location
        found.append(replace(item, posting=replace(item.posting, company=company, location=where,
                                                   apply_url=item.posting.url)))
    return found


_PARSERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "workable": _workable,
            "smartrecruiters": _smartrecruiters, "recruitee": _recruitee, "personio": _personio, "breezy": _breezy}


def fetch_board(ats: str, token: str, company: str, get) -> list[FeedPosting]:
    url = _URLS[ats].format(token=token)
    if ats == "teamtailor":
        return _teamtailor(token, company, _get_bytes(get, url))
    return _PARSERS[ats](token, company, _get_json(get, url, **_PARAMS.get(ats, {})))


def board_exists(ats: str, token: str, get) -> bool:
    try:
        response = get(_URLS[ats].format(token=token), params=_PARAMS.get(ats), timeout=15)
        return response.status_code == 200 and isinstance(response.json(), (dict, list))
    except Exception:
        return False


def detect_board(name: str, get) -> tuple[str, str] | None:
    """The first Greenhouse, Lever or Ashby board that answers for one of the company's likely board names."""
    for token in slug_candidates(name):
        for ats in GUESSED:
            if board_exists(ats, token, get):
                return ats, token
    return None
