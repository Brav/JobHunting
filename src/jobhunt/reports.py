"""Read models for the dashboard: run status, daily reports, tracker and listing rows."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import String, func
from sqlmodel import Session, or_, select

from jobhunt.models import Job, Listing, Match, Run, RunListing
from jobhunt.timeutil import local_date, local_day_bounds_utc

STATUS_FILTERS = ("default", "all", "new", "interested", "applied", "hidden")


@dataclass
class RunSummary:
    run: Run | None
    total: int
    ok: int
    failures: list[tuple[str, str]]


@dataclass
class ReportSection:
    listing: Listing
    jobs: list[Job]
    error: str | None


@dataclass
class ListingRow:
    listing: Listing
    last: RunListing | None


def run_summary(session: Session) -> RunSummary:
    run = session.exec(select(Run).order_by(Run.started_at.desc(), Run.id.desc())).first()
    if run is None:
        return RunSummary(run=None, total=0, ok=0, failures=[])
    rows = session.exec(
        select(RunListing, Listing)
        .join(Listing, Listing.id == RunListing.listing_id)
        .where(RunListing.run_id == run.id)
        .order_by(Listing.id)
    ).all()
    failures = [(listing.name, row.error) for row, listing in rows if row.error]
    return RunSummary(run=run, total=len(rows), ok=len(rows) - len(failures), failures=failures)


def report_dates(session: Session, tz: ZoneInfo) -> list[date]:
    return sorted({local_date(started, tz) for started in session.exec(select(Run.started_at))})


def _report_order(job: Job):
    # Newest posting first, undated last, then by title.
    return (job.posted_at is None, -(job.posted_at.toordinal() if job.posted_at else 0), job.title)


def report_for_date(session: Session, day: date, tz: ZoneInfo) -> list[ReportSection]:
    start, end = local_day_bounds_utc(day, tz)
    run_ids = session.exec(
        select(Run.id).where(Run.started_at >= start, Run.started_at < end).order_by(Run.started_at, Run.id)
    ).all()
    if not run_ids:
        return []
    order = {run_id: i for i, run_id in enumerate(run_ids)}
    latest: dict[int, RunListing] = {}
    for row in sorted(session.exec(select(RunListing).where(RunListing.run_id.in_(run_ids))),
                      key=lambda r: order[r.run_id]):
        latest[row.listing_id] = row
    jobs_by_listing: dict[int, list[Job]] = defaultdict(list)
    for listing_id, job in session.exec(
        select(Match.listing_id, Job)
        .join(Job, Job.id == Match.job_id)
        .where(Match.run_id.in_(run_ids), Match.is_new == True, Job.status != "hidden")  # noqa: E712
    ):
        jobs_by_listing[listing_id].append(job)
    listings = session.exec(select(Listing).where(Listing.id.in_(latest)).order_by(Listing.id)).all()
    return [ReportSection(listing=listing, jobs=sorted(jobs_by_listing[listing.id], key=_report_order),
                          error=latest[listing.id].error)
            for listing in listings]


def tracker_jobs(session: Session, *, status: str = "default", listing_id: int | None = None,
                 q: str = "", page: int = 1, per_page: int = 50) -> tuple[list[Job], int]:
    if status not in STATUS_FILTERS:
        raise ValueError(f"unknown status filter {status!r}")
    query = select(Job)
    if status == "default":
        query = query.where(Job.status != "hidden")
    elif status != "all":
        query = query.where(Job.status == status)
    if listing_id is not None:
        query = query.where(Job.id.in_(select(Match.job_id).where(Match.listing_id == listing_id)))
    needle = q.strip().lower()
    if needle:
        # py_lower is registered in db.make_engine; SQLite's own lower() ignores non-ASCII letters.
        query = query.where(or_(func.py_lower(Job.title, type_=String).contains(needle, autoescape=True),
                                func.py_lower(Job.company, type_=String).contains(needle, autoescape=True)))
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    jobs = session.exec(
        query.order_by(Job.first_seen_at.desc(), Job.id.desc()).offset((page - 1) * per_page).limit(per_page)
    ).all()
    return list(jobs), total


def listing_rows(session: Session) -> list[ListingRow]:
    rows = []
    for listing in session.exec(select(Listing).order_by(Listing.id)):
        last = session.exec(
            select(RunListing)
            .join(Run, Run.id == RunListing.run_id)
            .where(RunListing.listing_id == listing.id)
            .order_by(Run.started_at.desc(), Run.id.desc())
        ).first()
        rows.append(ListingRow(listing=listing, last=last))
    return rows
