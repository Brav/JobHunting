"""The daily run: search each active listing, record jobs, work out what is new."""

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from jobhunt.models import Job, Match, Run, RunListing
from jobhunt.sources.base import JobPosting

WINDOW_MIN_HOURS = 24
WINDOW_MAX_HOURS = 168
WINDOW_OVERLAP_HOURS = 2


@dataclass
class ListingResult:
    found: int
    new: int


def window_hours(last_success: datetime | None, now: datetime) -> int:
    """How far back to search: since the last success plus overlap, within 24 h – 7 days."""
    if last_success is None:
        return WINDOW_MAX_HOURS
    hours = math.ceil((now - last_success).total_seconds() / 3600) + WINDOW_OVERLAP_HOURS
    return max(WINDOW_MIN_HOURS, min(WINDOW_MAX_HOURS, hours))


def last_success_at(session: Session, listing_id: int) -> datetime | None:
    return session.exec(
        select(func.max(Run.started_at))
        .join(RunListing, RunListing.run_id == Run.id)
        .where(RunListing.listing_id == listing_id, RunListing.error.is_(None))
    ).one()


def record_results(session: Session, run_id: int, listing_id: int,
                   postings: list[JobPosting], now: datetime) -> ListingResult:
    unique = list({p.external_id: p for p in postings}.values())
    new = 0
    for posting in unique:
        job = session.exec(select(Job).where(Job.external_id == posting.external_id)).first()
        if job is None:
            job = Job(external_id=posting.external_id, first_seen_at=now, title=posting.title,
                      company=posting.company, location=posting.location, url=posting.url,
                      posted_at=posting.posted_at)
        else:
            job.title, job.company, job.location = posting.title, posting.company, posting.location
            job.url, job.posted_at = posting.url, posting.posted_at
        session.add(job)
        session.flush()
        seen_before = session.exec(
            select(Match).where(Match.listing_id == listing_id, Match.job_id == job.id)
        ).first() is not None
        session.add(Match(run_id=run_id, listing_id=listing_id, job_id=job.id, is_new=not seen_before))
        new += not seen_before
    session.commit()
    return ListingResult(found=len(unique), new=new)
