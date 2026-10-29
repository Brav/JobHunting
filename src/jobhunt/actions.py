"""Dashboard writes: job status changes, listing deletion and deleting a listing's results."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, update
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


def _found_by(listing_id: int):
    return select(Match.job_id).where(Match.job_id == Job.id, Match.listing_id == listing_id).exists()


def _deletable_results(listing_id: int):
    """The listing's jobs nobody decided on: undecided, no application steps, and no other listing found them."""
    other = select(Match.job_id).where(Match.job_id == Job.id, Match.listing_id != listing_id).exists()
    steps = select(ApplicationStep.job_id).where(ApplicationStep.job_id == Job.id).exists()
    return and_(_found_by(listing_id), Job.status == "new", ~other, ~steps)


def listing_results(session: Session, listing_id: int) -> tuple[int, int]:
    """(how many of the listing's jobs deleting its results removes, how many it keeps)."""
    deletable = session.exec(select(func.count()).select_from(Job).where(_deletable_results(listing_id))).one()
    found = session.exec(select(func.count()).select_from(Job).where(_found_by(listing_id))).one()
    return deletable, found - deletable


def delete_listing_results(session: Session, listing_id: int) -> int:
    """Delete the listing's undecided results (the listing stays and keeps searching); returns how many went.

    Jobs the user saved, applied to or hid, and jobs another listing also found, are kept. A job still posted can be
    found again by the next search.
    """
    ids = list(session.exec(select(Job.id).where(_deletable_results(listing_id))).all())
    if ids:
        session.exec(delete(Match).where(Match.job_id.in_(ids)))
        session.exec(delete(Job).where(Job.id.in_(ids)))  # sightings and contact links go with them (cascade)
    session.commit()
    return len(ids)


def mark_seen(session_factory, job_id: int, now: datetime) -> None:
    """Record the first time a job was shown; later views change nothing."""
    with session_factory() as s:
        s.exec(update(Job).where(Job.id == job_id, Job.seen_at.is_(None)).values(seen_at=now))
        s.commit()
