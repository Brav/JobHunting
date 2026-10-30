"""Watched companies: new jobs read straight from their Greenhouse, Lever or Ashby job boards (public JSON)."""

import html
import re
from datetime import date, datetime, timezone

from jobhunt.sources.base import JobPosting
from jobhunt.sources.feeds import FeedPosting, _get_json, _markdown

ATS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}
_URLS = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
         "lever": "https://api.lever.co/v0/postings/{token}",
         "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}"}
_PARAMS = {"greenhouse": {"content": "true"}, "lever": {"mode": "json"}, "ashby": {"includeCompensation": "true"}}


_PAGES = {"greenhouse": "https://job-boards.greenhouse.io/{t}", "lever": "https://jobs.lever.co/{t}",
          "ashby": "https://jobs.ashbyhq.com/{t}"}


def board_page_url(ats: str, token: str) -> str:
    """The company's public careers page on its hiring system."""
    return _PAGES[ats].format(t=token)


def slug_candidates(name: str) -> list[str]:
    """Board names to try for a company: "Acme & Co" → acmeco, acme-co, acme."""
    words = re.findall(r"[a-z0-9]+", name.casefold())
    return list(dict.fromkeys(s for s in ("".join(words), "-".join(words), words[0] if words else "") if s))


def _day(text: str | None) -> date | None:
    try:
        return datetime.fromisoformat(text).date() if text else None
    except ValueError:
        return None


def _posting(ats, token, company, job_id, title, location, url, posted, description, salary=None) -> FeedPosting:
    return FeedPosting(JobPosting(external_id=f"{ats}:{token}:{job_id}", title=title.strip(), company=company,
                                  location=location or "", url=url, posted_at=posted, description=description,
                                  source=ats, salary_text=salary, apply_url=url))


def fetch_board(ats: str, token: str, company: str, get) -> list[FeedPosting]:
    data = _get_json(get, _URLS[ats].format(token=token), **_PARAMS[ats])
    found = []
    if ats == "greenhouse":
        for j in data.get("jobs", []):
            found.append(_posting(ats, token, company, j["id"], j["title"], (j.get("location") or {}).get("name"),
                                  j.get("absolute_url", ""), _day(j.get("first_published") or j.get("updated_at")),
                                  _markdown(html.unescape(j.get("content") or ""))))
    elif ats == "lever":
        for j in data:
            where = (j.get("categories") or {}).get("location") or ""
            if j.get("workplaceType") in ("remote", "hybrid"):
                where = f"{where} ({j['workplaceType']})".strip()
            created = j.get("createdAt")
            found.append(_posting(ats, token, company, j["id"], j["text"], where, j.get("hostedUrl", ""),
                                  datetime.fromtimestamp(created / 1000, timezone.utc).date() if created else None,
                                  j.get("descriptionPlain") or _markdown(j.get("description"))))
    else:
        for j in data.get("jobs", []):
            pay = (j.get("compensation") or {}).get("compensationTierSummary")
            found.append(_posting(ats, token, company, j["id"], j["title"], j.get("location"), j.get("jobUrl", ""),
                                  _day(j.get("publishedAt")), j.get("descriptionPlain") or _markdown(j.get("descriptionHtml")),
                                  pay))
    return found


def board_exists(ats: str, token: str, get) -> bool:
    try:
        response = get(_URLS[ats].format(token=token), params=_PARAMS[ats], timeout=15)
        return response.status_code == 200 and isinstance(response.json(), (dict, list))
    except Exception:
        return False


def detect_board(name: str, get) -> tuple[str, str] | None:
    """The first Greenhouse, Lever or Ashby board that answers for one of the company's likely board names."""
    for token in slug_candidates(name):
        for ats in ATS:
            if board_exists(ats, token, get):
                return ats, token
    return None
