"""When searches run: a listing's own schedule, or the default from Settings.

A schedule is either some times of day ("times:08:00,13:00") or a start time repeated every N hours through the day
("every:00:00/6" → 00:00, 06:00, 12:00, 18:00). Times are local to the user's time zone; slots are naive UTC.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jobhunt.timeutil import local_date, local_to_utc

EVERY_CHOICES = (1, 2, 3, 4, 6, 8, 12)  # offered in forms; stored text may also hold any divisor of 24 (from .env)
MAX_TIMES = 24

_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


@dataclass(frozen=True)
class Schedule:
    kind: str  # "times" or "every"
    times: tuple[time, ...] = ()
    start: time | None = None
    every: int | None = None


def parse_time(value: str) -> time | None:
    """"8:30", "08:30" or "08:30:00" → 08:30; anything else → None."""
    m = _TIME.match(value.strip())
    if not m:
        return None
    hour, minute, second = int(m[1]), int(m[2]), int(m[3] or 0)
    return time(hour, minute) if hour < 24 and minute < 60 and second < 60 else None


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def parse(text: str | None) -> Schedule | None:
    """The stored text as a schedule; None for empty or unreadable text (the caller falls back to the default)."""
    kind, _, rest = (text or "").partition(":")
    if kind == "times":
        times = [parse_time(part) for part in rest.split(",")]
        if not rest or None in times:
            return None
        return Schedule("times", tuple(sorted(set(times))))
    if kind == "every":
        start_text, _, every_text = rest.partition("/")
        start = parse_time(start_text)
        if start is None or not every_text.isdecimal() or not 0 < int(every_text) <= 24 or 24 % int(every_text):
            return None
        return Schedule("every", start=start, every=int(every_text))
    return None


def to_text(s: Schedule) -> str:
    if s.kind == "times":
        return "times:" + ",".join(_hhmm(t) for t in s.times)
    return f"every:{_hhmm(s.start)}/{s.every}"


def from_env(run_at: time, every_hours: int) -> Schedule:
    """The default before the user sets one: JOBHUNT_RUN_AT every JOBHUNT_RUN_EVERY_HOURS (10:00 every 4 h)."""
    return Schedule("every", start=time(run_at.hour, run_at.minute), every=every_hours)


def times_of_day(s: Schedule) -> list[time]:
    if s.kind == "times":
        return sorted(set(s.times))
    start = s.start.hour * 60 + s.start.minute
    return sorted({time(*divmod((start + k * s.every * 60) % (24 * 60), 60)) for k in range(24 // s.every)})


def describe(s: Schedule) -> str:
    if s.kind == "times":
        return ", ".join(_hhmm(t) for t in times_of_day(s))
    return f"Every {s.every} h from {_hhmm(s.start)}"


def from_form(choice: str, times: list[str], start: str, every: str) -> tuple[str | None, list[str]]:
    """(the text to store, what's wrong); text None means the default schedule."""
    if choice == "times":
        entered = [value for value in times if value.strip()]
        parsed = [parse_time(value) for value in entered]
        if None in parsed:
            return None, ["Use times like 08:30"]
        if not parsed:
            return None, ["Add at least one time"]
        if len(set(parsed)) < len(parsed):
            return None, ["A time appears twice"]
        if len(parsed) > MAX_TIMES:
            return None, ["At most 24 times a day"]
        return to_text(Schedule("times", tuple(sorted(parsed)))), []
    if choice == "every":
        errors = []
        start_time = parse_time(start)
        if start_time is None:
            errors.append("Use times like 08:30")
        if not every.strip().isdecimal() or int(every) not in EVERY_CHOICES + (24,):  # 24: once a day, from .env
            errors.append("Choose how many hours")
        if errors:
            return None, errors
        return to_text(Schedule("every", start=start_time, every=int(every))), []
    return None, []


def _slots(s: Schedule, days: list[date], tz: ZoneInfo) -> list[datetime]:
    return [local_to_utc(day, t, tz) for day in days for t in times_of_day(s)]


def latest_slot(s: Schedule, now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent scheduled time at or before now (naive UTC)."""
    today = local_date(now, tz)
    return max(slot for slot in _slots(s, [today - timedelta(days=1), today], tz) if slot <= now)


def next_slot(s: Schedule, now: datetime, tz: ZoneInfo) -> datetime:
    """The first scheduled time after now (naive UTC)."""
    today = local_date(now, tz)
    return min(slot for slot in _slots(s, [today, today + timedelta(days=1)], tz) if slot > now)


def push_time(s: Schedule) -> time:
    """The daily phone push: half an hour after the schedule's start (or its first time)."""
    anchor = s.start if s.kind == "every" else times_of_day(s)[0]
    total = (anchor.hour * 60 + anchor.minute + 30) % (24 * 60)
    return time(*divmod(total, 60))


def form_values(stored: str | None) -> dict:
    """What a schedule form shows for stored text: the choice, the times, the start and the interval."""
    s = parse(stored)
    if s is None:
        return {"choice": "default", "times": [""], "start": "08:00", "every": "6"}
    if s.kind == "times":
        return {"choice": "times", "times": [_hhmm(t) for t in s.times], "start": "08:00", "every": "6"}
    return {"choice": "every", "times": [""], "start": _hhmm(s.start), "every": str(s.every)}
