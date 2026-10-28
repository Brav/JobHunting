"""Dashboard writes: job status changes and listing deletion."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, update
from sqlmodel import Session, select

from jobhunt.applications import on_status_change
from jobhunt.models import JOB_STATUSES, ApplicationStep, Job, Listing, Match, RunListing
from jobhunt.timeutil import local_date


def set_job_status(session: Session, job_id: int, status: str, now: datetime, tz: ZoneInfo = ZoneInfo("UTC")) -> Job:
    if status not in JOB_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(f"no job with id {job_id}")
    previous = job.status
    job.status, job.status_changed_at = status, now
    job.seen_at = job.seen_at or now
    on_status_change(session, job, previous, local_date(now, tz), now)
    session.add(job)
    session.commit()
    return job


def delete_listing(session: Session, listing_id: int) -> None:
    """Remove a listing and its history; keep any job the user acted on or another listing found."""
    session.exec(delete(Match).where(Match.listing_id == listing_id))
    session.exec(delete(RunListing).where(RunListing.listing_id == listing_id))
    session.exec(delete(Listing).where(Listing.id == listing_id))
    session.exec(delete(Job).where(Job.status == "new",
                                   ~select(Match.job_id).where(Match.job_id == Job.id).exists(),
                                   ~select(ApplicationStep.job_id).where(ApplicationStep.job_id == Job.id).exists()))
    session.commit()


def mark_seen(session_factory, job_id: int, now: datetime) -> None:
    """Record the first time a job was shown; later views change nothing."""
    with session_factory() as s:
        s.exec(update(Job).where(Job.id == job_id, Job.seen_at.is_(None)).values(seen_at=now))
        s.commit()
