"""LinkedIn job search through JobSpy. The only module that imports jobspy or pandas."""

import logging
import re
from collections.abc import Callable
from datetime import date, datetime

import pandas as pd

from jobhunt.sources.base import JobPosting, SourceBlocked, SourceError

BLOCKED_MESSAGE = "rate limited by LinkedIn"

logger = logging.getLogger(__name__)

_VIEW_ID = re.compile(r"/jobs/view/(\d+)")


def km_to_miles(km: int) -> int:
    return round(km / 1.609)


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _job_id(row) -> str | None:
    raw = _text(row.get("id"))
    if raw.startswith("li-") and raw[3:].isdigit():
        return raw[3:]
    match = _VIEW_ID.search(_text(row.get("job_url")))
    return match.group(1) if match else None


def _posted(value) -> date | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, datetime):  # also covers pd.Timestamp
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def postings_from_frame(df: pd.DataFrame) -> list[JobPosting]:
    postings = []
    for row in df.to_dict("records"):
        job_id, title = _job_id(row), _text(row.get("title"))
        if not job_id or not title:
            logger.warning("skipping LinkedIn row without id or title: %s", row.get("job_url"))
            continue
        postings.append(JobPosting(
            external_id=job_id,
            title=title,
            company=_text(row.get("company")),
            location=_text(row.get("location")),
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            posted_at=_posted(row.get("date_posted")),
        ))
    return postings


class LinkedInSource:
    def __init__(self, scrape: Callable[..., pd.DataFrame] | None = None):
        if scrape is None:
            from jobspy import scrape_jobs as scrape
        self._scrape = scrape

    def search(self, keywords: str, location: str, radius_km: int,
               window_hours: int, limit: int) -> list[JobPosting]:
        try:
            df = self._scrape(
                site_name=["linkedin"],
                search_term=keywords,
                location=location,
                distance=km_to_miles(radius_km),
                hours_old=window_hours,
                results_wanted=limit,
                fetch_description=False,
            )
        except Exception as e:
            raise SourceError(f"LinkedIn search failed: {e}") from e
        # JobSpy reports a failed scrape here rather than raising; results may be partial.
        error = df.attrs.get("errors", {}).get("linkedin")
        if error:
            if "429" in error or "blocked" in error.lower():
                raise SourceBlocked(BLOCKED_MESSAGE)
            raise SourceError(error)
        return postings_from_frame(df)
