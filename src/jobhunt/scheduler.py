"""When runs happen: every N hours from an anchor time, catch-up after missed slots, or on demand — one at a time."""

import logging
import threading
import time as _time
from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func
from sqlmodel import Session, or_, select

from jobhunt.config import Settings
from jobhunt.models import Run
from jobhunt.runner import run_daily
from jobhunt.sources.base import JobSource
from jobhunt.timeutil import local_date, local_to_utc, utcnow

logger = logging.getLogger(__name__)


CATCH_UP_CHECK_MINUTES = 15


def run_hours(run_at: time, every_hours: int) -> list[int]:
    """Local hours of the scheduled runs, e.g. 10:00 every 4 h → [2, 6, 10, 14, 18, 22]."""
    return sorted((run_at.hour + k * every_hours) % 24 for k in range(24 // every_hours))


def latest_slot(now: datetime, tz: ZoneInfo, run_at: time, every_hours: int) -> datetime:
    """The most recent scheduled run time at or before now (naive UTC)."""
    today = local_date(now, tz)
    candidates = (local_to_utc(day, time(hour, run_at.minute), tz)
                  for day in (today - timedelta(days=1), today)
                  for hour in run_hours(run_at, every_hours))
    return max(slot for slot in candidates if slot <= now)


def should_catch_up(now: datetime, tz: ZoneInfo, run_at: time, every_hours: int,
                    last_started: datetime | None) -> bool:
    """True when no run has started since the most recent scheduled run time."""
    return last_started is None or last_started < latest_slot(now, tz, run_at, every_hours)


def latest_run_started(session: Session) -> datetime | None:
    # An interrupted run (restart mid-run) must not count, or the same-day catch-up would be skipped.
    return session.exec(
        select(func.max(Run.started_at)).where(or_(Run.error.is_(None), Run.error != "interrupted"))
    ).one()


class RunCoordinator:
    def __init__(self, session_factory: Callable[[], Session], source: JobSource, settings: Settings, *,
                 clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], None] = _time.sleep):
        self.session_factory = session_factory
        self.source = source
        self.settings = settings
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._scheduler: BackgroundScheduler | None = None

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def _run(self, trigger: str) -> int:
        return run_daily(self.session_factory, self.source, trigger=trigger,
                         results_per_listing=self.settings.results_per_listing,
                         clock=self.clock, sleep=self.sleep)

    def run_blocking(self, trigger: str) -> int | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._run(trigger)
        finally:
            self._lock.release()

    def trigger(self, trigger: str) -> bool:
        if not self._lock.acquire(blocking=False):
            logger.info("a run is already in progress; %s trigger ignored", trigger)
            return False

        def work():
            try:
                self._run(trigger)
            finally:
                self._lock.release()

        threading.Thread(target=work, name=f"jobhunt-run-{trigger}", daemon=True).start()
        return True

    def catch_up_if_needed(self) -> bool:
        s = self.settings
        with self.session_factory() as session:
            last = latest_run_started(session)
        if not should_catch_up(self.clock(), s.tz, s.run_at, s.run_every_hours, last):
            return False
        logger.info("a scheduled run was missed; catching up now")
        return self.trigger("catch_up")

    def start(self) -> None:
        s = self.settings
        hours = run_hours(s.run_at, s.run_every_hours)
        self._scheduler = BackgroundScheduler(timezone=s.tz)
        self._scheduler.add_job(self.trigger, CronTrigger(hour=",".join(map(str, hours)), minute=s.run_at.minute,
                                                          timezone=s.tz),
                                args=["scheduled"], id="scheduled", coalesce=True, misfire_grace_time=3600)
        self._scheduler.add_job(self.catch_up_if_needed, "interval", minutes=CATCH_UP_CHECK_MINUTES,
                                id="catch_up_check", coalesce=True)
        self._scheduler.start()
        logger.info("scheduler started; runs at %s %s",
                    ", ".join(f"{h:02d}:{s.run_at.minute:02d}" for h in hours), s.tz.key)
        self.catch_up_if_needed()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def next_run_at(self) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job("scheduled")
        return job.next_run_time if job else None
