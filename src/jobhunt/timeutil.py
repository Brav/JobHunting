"""Time helpers. Stored datetimes are naive UTC; display converts to local time."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=UTC).astimezone(tz)


def local_date(dt: datetime, tz: ZoneInfo) -> date:
    return to_local(dt, tz).date()


def local_to_utc(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def local_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Half-open [start, end) of a local calendar day, as naive UTC."""
    return local_to_utc(day, time(0, 0), tz), local_to_utc(day + timedelta(days=1), time(0, 0), tz)
