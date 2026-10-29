"""The user's working preferences (skills, follow-ups, goals, pause, sources, push, default schedule), stored as JSON
in settings."""

import json
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime

from sqlmodel import Session

from jobhunt.models import Setting

PREFERENCES_KEY = "preferences"
FEED_ERRORS_KEY = "feed_errors"
SOURCES = {"remoteok": "Remote OK", "himalayas": "Himalayas", "hn": "Hacker News “Who is hiring”"}


@dataclass(frozen=True)
class Preferences:
    skills: tuple[str, ...] = ()  # empty = not set yet; jobhunt.fit fills it from the CV
    follow_up_days: int = 7
    auto_close_days: int = 30
    goal_applications_per_week: int = 5
    goal_reviews_per_week: int = 50
    paused_until: str = ""  # ISO date; scheduled searches and pushes wait until after it
    sources: tuple[str, ...] = tuple(SOURCES)
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


def save_feed_errors(session: Session, errors: dict[str, str], now: datetime) -> None:
    before = load_feed_errors(session)
    data = {name: {"error": message, "since": before.get(name, {}).get("since") or now.isoformat(timespec="minutes")}
            for name, message in errors.items()}
    row = session.get(Setting, FEED_ERRORS_KEY) or Setting(key=FEED_ERRORS_KEY, value="")
    row.value, row.updated_at = json.dumps(data), now
    session.add(row)
    session.commit()
