"""The user's working preferences (skills, follow-ups, goals, pause, default sites, push, default schedule), stored as JSON
in settings."""

import json
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime

from sqlmodel import Session

from jobhunt.models import Setting

PREFERENCES_KEY = "preferences"
FEED_ERRORS_KEY = "feed_errors"
DEFAULT_SITES = ("linkedin", "remoteok", "himalayas", "hn")  # jobhunt.sites.BUILTIN keys; kept here to avoid a cycle


@dataclass(frozen=True)
class Preferences:
    skills: tuple[str, ...] = ()  # empty = not set yet; jobhunt.fit fills it from the CV
    follow_up_days: int = 7
    auto_close_days: int = 30
    goal_applications_per_week: int = 5
    goal_reviews_per_week: int = 50
    paused_until: str = ""  # ISO date; scheduled searches and pushes wait until after it
    sites: tuple[str, ...] = DEFAULT_SITES  # the sites a listing without its own list searches
    ntfy_url: str = ""  # phone push for the daily digest; empty = nothing is sent
    last_digest_at: str = ""  # ISO datetime (naive UTC) the digest was last marked read
    schedule: str = ""  # the default search schedule (jobhunt.schedule text); empty = JOBHUNT_RUN_AT every N hours
    schedule_changed_at: str = ""  # ISO datetime (naive UTC) the default last changed


def load_preferences(session: Session) -> Preferences:
    """The saved preferences; missing or unreadable values fall back to their defaults."""
    row = session.get(Setting, PREFERENCES_KEY)
    try:
        data = json.loads(row.value) if row else {}
    except ValueError:
        data = {}
    defaults = Preferences()
    values = {}
    for f in fields(Preferences):
        value = data.get(f.name, getattr(defaults, f.name))
        default = getattr(defaults, f.name)
        if isinstance(default, tuple):
            value = tuple(value) if isinstance(value, (list, tuple)) else default
        elif not isinstance(value, type(default)):
            value = default
        values[f.name] = value
    if not data.get("sites") or not isinstance(data["sites"], list):  # saved before sites: LinkedIn plus the old sources
        old = data.get("sources")
        old = tuple(x for x in old if isinstance(x, str)) if isinstance(old, list) else ("remoteok", "himalayas", "hn")
        values["sites"] = ("linkedin", *old)
    return Preferences(**values)


def save_preferences(session: Session, prefs: Preferences, now: datetime) -> None:
    row = session.get(Setting, PREFERENCES_KEY) or Setting(key=PREFERENCES_KEY, value="")
    row.value, row.updated_at = json.dumps(asdict(prefs)), now
    session.add(row)
    session.commit()


def is_paused(prefs: Preferences, today: date) -> bool:
    """Scheduled searches and pushes wait while today is on or before the pause date."""
    try:
        return bool(prefs.paused_until) and today <= date.fromisoformat(prefs.paused_until)
    except ValueError:
        return False


def load_feed_errors(session: Session) -> dict[str, dict]:
    """The extra job sources failing now: {name: {"error": message, "since": ISO time (UTC)}}."""
    row = session.get(Setting, FEED_ERRORS_KEY)
    try:
        data = json.loads(row.value) if row else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def drop_feed_error(session: Session, name: str, now: datetime) -> None:
    """Forget one source's stored failure (an own site that was removed); the caller commits."""
    errors = load_feed_errors(session)
    if errors.pop(name, None) is not None:
        row = session.get(Setting, FEED_ERRORS_KEY)
        row.value, row.updated_at = json.dumps(errors), now
        session.add(row)


def save_feed_errors(session: Session, errors: dict[str, str], now: datetime) -> None:
    before = load_feed_errors(session)
    data = {name: {"error": message, "since": before.get(name, {}).get("since") or now.isoformat(timespec="minutes")}
            for name, message in errors.items()}
    row = session.get(Setting, FEED_ERRORS_KEY) or Setting(key=FEED_ERRORS_KEY, value="")
    row.value, row.updated_at = json.dumps(data), now
    session.add(row)
    session.commit()
