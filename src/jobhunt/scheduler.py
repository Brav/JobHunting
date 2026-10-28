"""When runs happen: daily at the configured time, on startup catch-up, or on demand — one at a time."""

import logging
import threading
import time as _time
from collections.abc import Callable
from datetime import datetime, time
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


def should_catch_up(now: datetime, tz: ZoneInfo, run_at: time, last_started: datetime | None) -> bool:
    """True when today's run time has passed and no run has started since then."""
    today_run = local_to_utc(local_date(now, tz), run_at, tz)
    return now >= today_run and (last_started is None or last_started < today_run)


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

    def start(self) -> None:
        s = self.settings
        self._scheduler = BackgroundScheduler(timezone=s.tz)
        self._scheduler.add_job(self.trigger, CronTrigger(hour=s.run_at.hour, minute=s.run_at.minute, timezone=s.tz),
                                args=["scheduled"], id="daily", coalesce=True, misfire_grace_time=3600)
        self._scheduler.start()
        logger.info("scheduler started; daily run at %s %s", s.run_at.strftime("%H:%M"), s.tz.key)
        with self.session_factory() as session:
            last = latest_run_started(session)
        if should_catch_up(self.clock(), s.tz, s.run_at, last):
            logger.info("today's run was missed; catching up now")
            self.trigger("catch_up")

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def next_run_at(self) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job("daily")
        return job.next_run_time if job else None
