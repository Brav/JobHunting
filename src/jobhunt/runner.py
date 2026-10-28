"""The daily run: search each active listing, record jobs, work out what is new."""

import logging
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from jobhunt.dedup import is_matchable, match_key
from jobhunt.models import Job, Listing, Match, Run, RunListing
from jobhunt.sources.base import JobPosting, JobSource, SourceError
from jobhunt.timeutil import utcnow

WINDOW_MIN_HOURS = 12
WINDOW_MAX_HOURS = 168
WINDOW_OVERLAP_HOURS = 2
RETRY_DELAYS_SECONDS = (120, 600)
PAUSE_RANGE_SECONDS = (3, 8)

logger = logging.getLogger(__name__)


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
    """Start of the listing's last successful run, ignoring runs from before its search was last edited."""
    query = (select(func.max(Run.started_at))
             .join(RunListing, RunListing.run_id == Run.id)
             .where(RunListing.listing_id == listing_id, RunListing.error.is_(None)))
    listing = session.get(Listing, listing_id)
    if listing is not None and listing.search_changed_at is not None:
        query = query.where(Run.started_at >= listing.search_changed_at)
    return session.exec(query).one()


def _unique_postings(postings: list[JobPosting]) -> list[JobPosting]:
    """One posting per LinkedIn ID, then per match key; the first occurrence wins."""
    ids: set[str] = set()
    by_key: dict[str, JobPosting] = {}
    for posting in postings:
        if posting.external_id not in ids:
            ids.add(posting.external_id)
            key = match_key(posting.title, posting.company)
            by_key.setdefault(key if is_matchable(key) else posting.external_id, posting)  # IDs have no "|"
    return list(by_key.values())


def _is_newer(posting: JobPosting, job: Job) -> bool:
    return posting.posted_at is not None and (job.posted_at is None or posting.posted_at > job.posted_at)


def record_results(session: Session, run_id: int, listing_id: int,
                   postings: list[JobPosting], now: datetime) -> ListingResult:
    """Record one listing's results. A job is new only the first time any listing finds it."""
    matched: set[int] = set()
    new = 0
    for posting in _unique_postings(postings):
        key = match_key(posting.title, posting.company)
        job = session.exec(select(Job).where(Job.external_id == posting.external_id)).first()
        if job is not None and job.id not in matched:
            if posting.title != job.title:
                job.title_en = job.translation_error = None  # the background pass translates the new title
            job.title, job.company, job.location = posting.title, posting.company, posting.location
            job.url, job.posted_at, job.match_key = posting.url, posting.posted_at, key
        elif job is None and is_matchable(key):
            job = session.exec(select(Job).where(Job.match_key == key).order_by(Job.id)).first()
            if job is not None and _is_newer(posting, job):
                # A newer repost of the same job: follow it, so the link and description stay live.
                job.external_id, job.url, job.posted_at = posting.external_id, posting.url, posting.posted_at
                job.description = job.description_fetched_at = job.details = job.description_error = None
                job.description_en = job.translation_error = None  # language and title_en stay: same title
        if job is None:
            job = Job(external_id=posting.external_id, match_key=key, first_seen_at=now,
                      title=posting.title, company=posting.company, location=posting.location,
                      url=posting.url, posted_at=posting.posted_at)
        # New means first found now; a job kept after its listing was deleted has lost its matches but isn't new.
        is_new = job.id is None
        session.add(job)
        session.flush()
        if job.id in matched:
            continue  # two postings resolved to the same job
        matched.add(job.id)
        session.add(Match(run_id=run_id, listing_id=listing_id, job_id=job.id, is_new=is_new))
        new += is_new
    session.commit()
    return ListingResult(found=len(matched), new=new)


def _save_attempt(session_factory, run_id: int, listing_id: int,
                  result: ListingResult | None = None, error: str | None = None) -> None:
    with session_factory() as s:
        row = s.get(RunListing, (run_id, listing_id)) or RunListing(run_id=run_id, listing_id=listing_id)
        row.attempts += 1
        row.error = error
        if result is not None:
            row.found, row.new = result.found, result.new
        s.add(row)
        s.commit()


def _attempt(session_factory, source: JobSource, run_id: int, listing: Listing,
             window: int, limit: int, clock: Callable[[], datetime]) -> str:
    """Search one listing. Returns "ok", "failed", or "skipped" (deleted or switched off meanwhile)."""
    with session_factory() as s:
        current = s.get(Listing, listing.id)
    if current is None or not current.active:
        logger.info("listing %r was deleted or switched off; skipping", listing.name)
        return "skipped"
    try:
        try:
            postings = source.search(keywords=current.keywords, location=current.location,
                                     radius_km=current.radius_km, window_hours=window, limit=limit)
        except SourceError as e:
            logger.warning("listing %r failed: %s", current.name, e)
            _save_attempt(session_factory, run_id, current.id, error=str(e))
            return "failed"
        with session_factory() as s:
            result = record_results(s, run_id, current.id, postings, clock())
        _save_attempt(session_factory, run_id, current.id, result=result)
    except IntegrityError:
        # The listing was deleted while it was being searched; foreign keys stop orphan rows.
        logger.info("listing %r was deleted during its search; skipping", current.name)
        return "skipped"
    logger.info("listing %r: found %d, new %d", current.name, result.found, result.new)
    return "ok"


def run_daily(session_factory: Callable[[], Session], source: JobSource, *, trigger: str,
              results_per_listing: int = 100, clock: Callable[[], datetime] = utcnow,
              sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> int:
    """Run every active listing, retrying failures; returns the run ID. Never raises."""
    rng = rng or random.Random()
    with session_factory() as s:
        run = Run(trigger=trigger, started_at=clock())
        s.add(run)
        s.commit()
        run_id = run.id
    logger.info("run %d started (%s)", run_id, trigger)

    error = None
    try:
        with session_factory() as s:
            listings = s.exec(select(Listing).where(Listing.active == True).order_by(Listing.id)).all()  # noqa: E712
        windows: dict[int, int] = {}
        succeeded = 0
        pending = list(listings)
        for pass_no in range(1 + len(RETRY_DELAYS_SECONDS)):
            if pass_no:
                if not pending:
                    break
                sleep(RETRY_DELAYS_SECONDS[pass_no - 1])
            failed = []
            for i, listing in enumerate(pending):
                if i:
                    sleep(rng.uniform(*PAUSE_RANGE_SECONDS))
                if listing.id not in windows:
                    with session_factory() as s:
                        windows[listing.id] = window_hours(last_success_at(s, listing.id), clock())
                outcome = _attempt(session_factory, source, run_id, listing,
                                   windows[listing.id], results_per_listing, clock)
                if outcome == "failed":
                    failed.append(listing)
                succeeded += outcome == "ok"
            pending = failed
        if not pending:
            status = "ok"
        else:
            status = "partial" if succeeded else "failed"
    except Exception as e:
        logger.exception("run %d crashed", run_id)
        status, error = "failed", f"{type(e).__name__}: {e}"

    with session_factory() as s:
        run = s.get(Run, run_id)
        run.status, run.error, run.finished_at = status, error, clock()
        s.add(run)
        s.commit()
    logger.info("run %d finished: %s", run_id, status)
    return run_id
