"""Everything decided about a job from its texts — eligibility, and the checks later features add — in one place.

Every place that changes a job's texts (search results, download, translation), the startup check for jobs decided
by older rules, and saving the settings call this module, so each check runs at the same moments.
"""

import json
from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, or_, select

from jobhunt import blocks, eligibility, fit, salary, scam, signals
from jobhunt.models import Job, Sighting
from jobhunt.preferences import Preferences, load_preferences
from jobhunt.timeutil import utcnow


@dataclass
class CheckContext:
    """What the checks need besides the job, loaded once per batch."""
    profile: eligibility.Profile
    prefs: Preferences
    blocked: set[str] = field(default_factory=set)
    rules: list = field(default_factory=list)
    today: date = field(default_factory=lambda: utcnow().date())


def load_context(session: Session) -> CheckContext:
    return CheckContext(eligibility.load_profile(session), load_preferences(session),
                        blocked=blocks.blocked_keys(session), rules=blocks.active_rules(session))


def refresh(session: Session, job: Job, ctx: CheckContext) -> None:
    """Decide everything about the job again from its current texts; the user's own choices are left alone."""
    eligibility.refresh(session, job, ctx.profile)
    sightings = list(session.exec(select(Sighting).where(Sighting.job_id == job.id)).all()) if job.id else []
    job.reposts = signals.reposts(sightings)
    score, reasons = signals.ghost(job, sightings, ctx.today)
    job.ghost_risk, job.ghost_reasons = score, json.dumps(reasons, ensure_ascii=False)
    job.blocked_by = blocks.blocked_reason(job, ctx.blocked, ctx.rules)
    flags = scam.scam_flags(job)
    pay = next((found for text in (job.salary_source_text, job.title, job.description_en or job.description)
                if (found := salary.parse_salary(text))), None)
    job.salary_min_eur, job.salary_max_eur, job.salary_text = \
        (pay.min_eur, pay.max_eur, pay.text) if pay else (None, None, None)
    matched, missing, score = fit.fit(" ".join(t for t in (job.title, job.description_en or job.description) if t),
                                      ctx.prefs.skills)
    job.fit_matched, job.fit_missing, job.fit_score = json.dumps(matched), json.dumps(missing), score
    job.best_score = fit.rank(job)
    job.scam_flags = json.dumps(flags, ensure_ascii=False) if flags else None


def refresh_job(session_factory, job_id: int) -> None:
    with session_factory() as s:
        job = s.get(Job, job_id)
        if job is not None:
            refresh(s, job, load_context(s))
            s.commit()


def refresh_all(session: Session, ctx: CheckContext | None = None, *, stale_only: bool = False) -> int:
    """Check every job (or only those checked by older rules); returns how many were checked."""
    ctx = ctx or load_context(session)
    stmt = select(Job)
    if stale_only:
        stmt = stmt.where(or_(Job.eligibility_rules.is_(None), Job.eligibility_rules < eligibility.RULES_VERSION))
    jobs = session.exec(stmt).all()
    for job in jobs:
        refresh(session, job, ctx)
    session.commit()
    return len(jobs)
