"""How fresh a job is and whether it looks like a ghost: sightings across runs, reposts, applicant counts, and
whether LinkedIn still accepts applications. Rules of thumb, not proof — shown as warnings, never used to hide."""

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from jobhunt.models import Job, Sighting
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError

OLD_DAYS = 30
CROWDED = 200
LIKELY_GHOST = 2


def applicant_count(text: str | None) -> int | None:
    """200 for "Over 200 applicants", 0 for "Be among the first 25 applicants", None when unknown."""
    if not text:
        return None
    if "among the first" in text.lower():
        return 0
    found = re.search(r"\d[\d,.]*", text)
    return int(re.sub(r"[,.]", "", found.group(0))) if found else None


def record_sighting(session: Session, job: Job, external_id: str, posted_at: date | None, now: datetime) -> None:
    """One sighting per job per run (a run's listings may all find the same job)."""
    recent = session.exec(select(Sighting.id).where(Sighting.job_id == job.id,
                                                    Sighting.seen_at >= now - timedelta(hours=1))).first()
    if recent is None:
        session.add(Sighting(job_id=job.id, seen_at=now, external_id=external_id, posted_at=posted_at))
        session.flush()


def reposts(sightings: list[Sighting]) -> int:
    """How often the job came back: under a new LinkedIn id, or with a later posting date than before."""
    ordered = sorted(sightings, key=lambda s: s.seen_at)
    later, newest = 0, None
    for s in ordered:
        if s.posted_at is not None:
            if newest is not None and s.posted_at > newest:
                later += 1
            newest = max(newest, s.posted_at) if newest else s.posted_at
    return max(later, len({s.external_id for s in ordered}) - 1, 0)


def ghost(job: Job, sightings: list[Sighting], today: date) -> tuple[int, list[str]]:
    """A 0–3 ghost-risk score with its reasons: long open, reposted, swamped with applicants at once."""
    score, reasons = 0, []
    days = (today - job.first_seen_at.date()).days if job.first_seen_at else 0
    if days > OLD_DAYS:
        score += 1
        reasons.append(f"First seen {days} days ago")
    times = reposts(sightings)
    if times:
        score += 2 if times >= 2 else 1
        reasons.append(f"Reposted {times}×")
    if (job.applicants or 0) >= CROWDED and job.posted_at and job.description_fetched_at \
            and (job.description_fetched_at.date() - job.posted_at).days <= 2:
        score += 1
        reasons.append(f"Over {CROWDED} applicants within 2 days")
    return min(score, 3), reasons


def check_closed(session_factory, fetch_page: Callable[[str], JobPage], now: datetime, *,
                 sleep: Callable[[float], None], blocked: Callable[[], bool] = lambda: False,
                 note_blocked: Callable[[], None] = lambda: None) -> int:
    """Look again at saved and applied jobs on LinkedIn; mark those no longer accepting applications."""
    with session_factory() as s:
        jobs = [(j.id, j.external_id) for j in s.exec(select(Job).where(
            Job.status.in_(("interested", "applied")), Job.closed_at.is_(None),
            (Job.source == "linkedin") | Job.source.is_(None))).all()]
    closed = 0
    for i, (job_id, external_id) in enumerate(jobs):
        if blocked():
            break
        if i:
            sleep(3)
        try:
            page = fetch_page(external_id)
        except SourceBlocked:
            note_blocked()
            break
        except SourceError:
            continue
        if page.closed:
            with session_factory() as s:
                job = s.get(Job, job_id)
                job.closed_at = now
                s.add(job)
                s.commit()
            closed += 1
    return closed
