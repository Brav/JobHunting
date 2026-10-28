"""Numbers for the Stats page: triage counts, applications, pipeline, weekly activity and breakdowns.

Everything is computed in Python from the applied jobs and their steps; there are hundreds of rows, not millions.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from jobhunt.applications import END_STAGES, INTERVIEW_STAGES, PIPELINE, REPLY_STAGES, STALE_DAYS
from jobhunt.language import language_name
from jobhunt.models import ApplicationStep, Job, Listing, Match
from jobhunt.timeutil import local_date
from jobhunt.workplace import WORKPLACE_LABELS


@dataclass(frozen=True)
class Application:
    job: Job
    steps: list[ApplicationStep]  # oldest first
    listings: list[str]
    applied_on: date | None  # earliest Applied step
    first_reply_on: date | None  # earliest step that is a reply (an interview, an offer or a rejection)
    latest_on: date | None  # date of the latest step

    @property
    def reached(self) -> int:
        """Index in PIPELINE of the furthest pipeline stage reached; every application reached Applied."""
        return max((PIPELINE.index(s.stage) for s in self.steps if s.stage in PIPELINE), default=0)


def _applications(session: Session, jobs: list[Job]) -> list[Application]:
    ids = [job.id for job in jobs]
    if not ids:
        return []
    steps, names = defaultdict(list), defaultdict(set)
    for step in session.exec(select(ApplicationStep).where(ApplicationStep.job_id.in_(ids))).all():
        steps[step.job_id].append(step)
    for job_id, name in session.exec(select(Match.job_id, Listing.name).join(Listing, Listing.id == Match.listing_id)
                                     .where(Match.job_id.in_(ids))).all():
        names[job_id].add(name)
    apps = []
    for job in jobs:
        own = sorted(steps[job.id], key=lambda s: (s.on_date, s.created_at, s.id))
        apps.append(Application(
            job=job, steps=own, listings=sorted(names[job.id]),
            applied_on=min((s.on_date for s in own if s.stage == "applied"), default=None),
            first_reply_on=min((s.on_date for s in own if s.stage in REPLY_STAGES), default=None),
            latest_on=max((s.on_date for s in own), default=None)))
    return apps


def load_applications(session: Session) -> list[Application]:
    return _applications(session, list(session.exec(select(Job).where(Job.status == "applied")).all()))


def load_application(session: Session, job_id: int) -> Application | None:
    job = session.get(Job, job_id)
    return _applications(session, [job])[0] if job and job.status == "applied" else None


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True)
class Tile:
    label: str
    total: int
    this_week: int


def overview(session: Session, today: date, tz: ZoneInfo) -> list[Tile]:
    week = _week_start(today)
    jobs = session.exec(select(Job.status, Job.first_seen_at, Job.status_changed_at)).all()

    def tile(label, rows, when):
        rows = list(rows)
        return Tile(label, len(rows), sum(1 for r in rows if when(r) and local_date(when(r), tz) >= week))

    applied = load_applications(session)
    return [
        tile("Found", jobs, lambda r: r.first_seen_at),
        tile("Undecided", (r for r in jobs if r.status == "new"), lambda r: r.first_seen_at),
        tile("Saved", (r for r in jobs if r.status == "interested"), lambda r: r.status_changed_at),
        Tile("Applied", len(applied), sum(1 for a in applied if a.applied_on and a.applied_on >= week)),
        tile("Hidden", (r for r in jobs if r.status == "hidden"), lambda r: r.status_changed_at),
    ]


@dataclass(frozen=True)
class AppRow:
    app: Application
    waiting: int | None  # days since the latest step
    stale: bool
    open: bool


def application_row(app: Application, today: date) -> AppRow:
    is_open = app.job.stage is None or app.job.stage in PIPELINE
    waiting = (today - app.latest_on).days if app.latest_on else None
    return AppRow(app, waiting, is_open and waiting is not None and waiting >= STALE_DAYS, is_open)


def application_rows(apps: list[Application], today: date, which: str) -> tuple[list[AppRow], dict[str, int]]:
    rows = [application_row(app, today) for app in apps]
    counts = {"open": sum(r.open for r in rows), "closed": sum(not r.open for r in rows), "all": len(rows)}
    shown = [r for r in rows if which == "all" or r.open == (which == "open")]
    shown.sort(key=lambda r: (r.waiting is None, -(r.waiting or 0), r.app.job.company.casefold()))
    return shown, counts


@dataclass(frozen=True)
class Pipeline:
    reached: list[tuple[str, int]]
    ended: list[tuple[str, int]]
    total: int
    reply_rate: int | None  # whole percent
    median_reply_days: float | None


def pipeline(apps: list[Application]) -> Pipeline:
    replied = [a for a in apps if a.first_reply_on]
    days = [max(0, (a.first_reply_on - a.applied_on).days) for a in replied if a.applied_on]
    return Pipeline(
        reached=[(stage, sum(1 for a in apps if a.reached >= i)) for i, stage in enumerate(PIPELINE)],
        ended=[(stage, sum(1 for a in apps if a.job.stage == stage)) for stage in END_STAGES],
        total=len(apps),
        reply_rate=round(100 * len(replied) / len(apps)) if apps else None,
        median_reply_days=statistics.median(days) if days else None,
    )


@dataclass(frozen=True)
class Week:
    start: date
    found: int
    applied: int
    hidden: int
    interviews: int


def activity(session: Session, apps: list[Application], today: date, tz: ZoneInfo, weeks: int = 12) -> list[Week]:
    """Weeks from the current one back, Monday to Sunday in the local time zone."""
    current = _week_start(today)
    counts = [defaultdict(int) for _ in range(weeks)]

    def count(day: date | None, column: str) -> None:
        if day is None:
            return
        index = (current - _week_start(day)).days // 7
        if 0 <= index < weeks:
            counts[index][column] += 1

    for status, first_seen_at, status_changed_at in session.exec(
            select(Job.status, Job.first_seen_at, Job.status_changed_at)).all():
        count(local_date(first_seen_at, tz), "found")
        if status == "hidden" and status_changed_at:
            count(local_date(status_changed_at, tz), "hidden")
    for app in apps:
        count(app.applied_on, "applied")
        for step in app.steps:
            if step.stage in INTERVIEW_STAGES:
                count(step.on_date, "interviews")
    return [Week(current - timedelta(weeks=i), c["found"], c["applied"], c["hidden"], c["interviews"])
            for i, c in enumerate(counts)]


@dataclass(frozen=True)
class BreakdownRow:
    label: str
    applied: int
    replied: int
    interviews: int
    offers: int


def breakdowns(apps: list[Application]) -> dict[str, list[BreakdownRow]]:
    """Applied / replied / interviews / offers per listing, workplace, language and seniority."""
    keys = {
        "Listing": lambda a: a.listings or ["Unknown"],  # a job found by two listings counts under both
        "Workplace": lambda a: [WORKPLACE_LABELS.get(a.job.workplace, "Unknown")],
        "Language": lambda a: [language_name(a.job.language) if a.job.language else "Unknown"],
        "Seniority": lambda a: [a.job.seniority or "Unknown"],
    }
    result = {}
    for name, labels_of in keys.items():
        groups = defaultdict(list)
        for app in apps:
            for label in labels_of(app):
                groups[label].append(app)
        rows = [BreakdownRow(label, len(group), sum(1 for a in group if a.first_reply_on),
                             sum(1 for a in group if a.reached >= 1), sum(1 for a in group if a.reached >= 4))
                for label, group in groups.items()]
        result[name] = sorted(rows, key=lambda r: (-r.applied, r.label))
    return result
