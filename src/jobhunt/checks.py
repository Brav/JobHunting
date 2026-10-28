"""Everything decided about a job from its texts — eligibility, and the checks later features add — in one place.

Every place that changes a job's texts (search results, download, translation), the startup check for jobs decided
by older rules, and saving the settings call this module, so each check runs at the same moments.
"""

from dataclasses import dataclass, field

from sqlmodel import Session, or_, select

from jobhunt import eligibility
from jobhunt.models import Job
from jobhunt.preferences import Preferences, load_preferences


@dataclass
class CheckContext:
    """What the checks need besides the job, loaded once per batch."""
    profile: eligibility.Profile
    prefs: Preferences
    blocked: set[str] = field(default_factory=set)
    rules: list = field(default_factory=list)


def load_context(session: Session) -> CheckContext:
    return CheckContext(eligibility.load_profile(session), load_preferences(session))


def refresh(session: Session, job: Job, ctx: CheckContext) -> None:
    """Decide everything about the job again from its current texts; the user's own choices are left alone."""
    eligibility.refresh(session, job, ctx.profile)


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
