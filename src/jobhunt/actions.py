"""Dashboard writes: job status changes and listing deletion."""

from datetime import datetime

from sqlalchemy import delete
from sqlmodel import Session, select

from jobhunt.models import JOB_STATUSES, Job, Listing, Match, RunListing


def set_job_status(session: Session, job_id: int, status: str, now: datetime) -> Job:
    if status not in JOB_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(f"no job with id {job_id}")
    job.status, job.status_changed_at = status, now
    session.add(job)
    session.commit()
    return job


def delete_listing(session: Session, listing_id: int) -> None:
    """Remove a listing and its history; keep any job the user acted on or another listing found."""
    session.exec(delete(Match).where(Match.listing_id == listing_id))
    session.exec(delete(RunListing).where(RunListing.listing_id == listing_id))
    session.exec(delete(Listing).where(Listing.id == listing_id))
    session.exec(delete(Job).where(Job.status == "new",
                                   ~select(Match.job_id).where(Match.job_id == Job.id).exists()))
    session.commit()
