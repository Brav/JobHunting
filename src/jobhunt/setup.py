"""First start: whether the user has set up their profile, and the upgrade rule for databases from before setup."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from jobhunt.eligibility import LEGACY_PROFILE, load_profile, profile_saved, save_profile
from jobhunt.models import Job, Listing, Setting

SKIPPED_KEY = "setup_skipped"


def setup_state(session: Session) -> str:
    """"done" once a profile is saved, "skipped" after "Skip for now", else "pending"."""
    if profile_saved(session):
        return "done"
    return "skipped" if session.get(Setting, SKIPPED_KEY) is not None else "pending"


def adopt_legacy_profile(session: Session, now: datetime) -> bool:
    """A database from before setup (it has jobs or listings, no profile) keeps the old built-in profile as its own."""
    if profile_saved(session):
        return False
    if session.exec(select(Job.id).limit(1)).first() is None and session.exec(select(Listing.id).limit(1)).first() is None:
        return False
    save_profile(session, LEGACY_PROFILE, now)
    return True


def skip_setup(session: Session, now: datetime) -> None:
    session.add(session.get(Setting, SKIPPED_KEY) or Setting(key=SKIPPED_KEY, value="1", updated_at=now))
    session.commit()


def time_zone(session: Session, fallback: ZoneInfo) -> ZoneInfo:
    """The saved profile's time zone; the fallback (TZ, else UTC) until one is saved."""
    return ZoneInfo(load_profile(session).time_zone) if profile_saved(session) else fallback


def fill_skills_from_cv(session: Session, path: Path | None) -> bool:
    """No skills listed yet: take the stack terms in the CV (read only); the user edits them in Settings."""
    from jobhunt import checks
    from jobhunt.cv import read_cv_text
    from jobhunt.fit import default_skills
    from jobhunt.preferences import load_preferences, save_preferences
    from jobhunt.timeutil import utcnow
    prefs = load_preferences(session)
    if prefs.skills or path is None:
        return False
    try:
        skills = default_skills(read_cv_text(path))
    except Exception:
        return False  # an unreadable CV: fit stays empty until the user lists skills
    if not skills:
        return False
    save_preferences(session, replace(prefs, skills=skills), utcnow())
    checks.refresh_all(session)
    return True
