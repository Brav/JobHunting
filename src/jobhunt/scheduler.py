"""When runs happen: every N hours from an anchor time, catch-up after missed slots, or on demand — one at a time."""

import logging
import threading
import time as _time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func
from sqlmodel import Session, or_, select

from jobhunt import checks
from jobhunt.backup import backup_now
from jobhunt.signals import check_closed
from jobhunt.config import Settings
from jobhunt.enrich import BLOCK_BACKOFF, ENRICH_EVERY_MINUTES, EnrichResult, enrich_pending, first_of_next_month
from jobhunt.models import Listing, Run
from jobhunt.runner import run_daily
from jobhunt.sources.base import JobPage, JobSource
from jobhunt.timeutil import local_date, local_to_utc, utcnow
from jobhunt.translate import Translator

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
                 clock: Callable[[], datetime] = utcnow, sleep: Callable[[float], None] = _time.sleep,
                 fetch_page: Callable[[str], JobPage] | None = None, translator: Translator | None = None):
        self.session_factory = session_factory
        self.source = source
        self.settings = settings
        self.clock = clock
        self.sleep = sleep
        self.post = None  # tests may set a fake HTTP POST for the digest push
        self.fetch_page = fetch_page
        self.translator = translator
        self._lock = threading.Lock()
        self._enrich_lock = threading.Lock()
        self._blocked_until: datetime | None = None
        self._titles_paused_until: datetime | None = None
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
        if trigger != "manual" and self._paused():
            logger.info("searches are paused; %s run skipped", trigger)
            return False
        if trigger != "manual" and not self._has_listings():
            logger.info("no active listings yet; %s run skipped", trigger)  # a new install: nothing to search
            return False
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

    def _has_listings(self) -> bool:
        with self.session_factory() as s:
            return s.exec(select(Listing.id).where(Listing.active == True).limit(1)).first() is not None  # noqa: E712

    def _paused(self) -> bool:
        from jobhunt.preferences import is_paused, load_preferences
        with self.session_factory() as s:
            return is_paused(load_preferences(s), local_date(utcnow(), self.settings.tz))

    def catch_up_if_needed(self) -> bool:
        s = self.settings
        with self.session_factory() as session:
            last = latest_run_started(session)
        if not should_catch_up(self.clock(), s.tz, s.run_at, s.run_every_hours, last):
            return False
        logger.info("a scheduled run was missed; catching up now")
        return self.trigger("catch_up")

    def linkedin_blocked(self) -> bool:
        """True while backing off after LinkedIn blocked a request (background pass or a job page)."""
        return self._blocked_until is not None and self.clock() < self._blocked_until

    def note_blocked(self) -> None:
        self._blocked_until = self.clock() + BLOCK_BACKOFF

    def enrich_if_idle(self) -> EnrichResult | None:
        """One background pass, unless disabled, a search or another pass is running, or LinkedIn blocked us."""
        if self.fetch_page is None or self.is_running:
            return None
        if not self._enrich_lock.acquire(blocking=False):
            return None
        try:
            now = self.clock()
            if self.linkedin_blocked():
                return None
            titles = self._titles_paused_until is None or now >= self._titles_paused_until
            result = enrich_pending(self.session_factory, self.fetch_page, self.translator,
                                    should_stop=lambda: self.is_running, translate_titles=titles,
                                    clock=self.clock, sleep=self.sleep)
            if result.blocked:
                self.note_blocked()
            if result.quota_exceeded:
                self._titles_paused_until = first_of_next_month(self.clock())
            if result.downloaded or result.failed or result.blocked or result.titles or result.quota_exceeded:
                logger.info("enrichment: downloaded %d, failed %d, titles translated %d%s%s", result.downloaded,
                            result.failed, result.titles, ", blocked by LinkedIn" if result.blocked else "",
                            ", DeepL limit reached" if result.quota_exceeded else "")
            return result
        finally:
            self._enrich_lock.release()

    def nightly_backup(self) -> None:
        try:
            path = backup_now(self.settings.db_path, self.settings.db_path.parent / "backups",
                              datetime.now(self.settings.tz).replace(tzinfo=None))
            logger.info("backed up to %s", path.name)
        except Exception:
            logger.exception("nightly backup failed")

    def nightly_recheck(self) -> None:
        """Ages change daily (ghost risk counts days open): decide every job again."""
        try:
            with self.session_factory() as s:
                logger.info("re-checked %d jobs", checks.refresh_all(s))
        except Exception:
            logger.exception("nightly re-check failed")

    def closed_check(self) -> None:
        try:
            closed = check_closed(self.session_factory, self.fetch_page, utcnow(), sleep=self.sleep,
                                  blocked=self.linkedin_blocked, note_blocked=self.note_blocked)
            logger.info("%d saved or applied jobs no longer accept applications", closed)
        except Exception:
            logger.exception("closed check failed")

    def daily_auto_close(self) -> None:
        from jobhunt.followups import auto_close
        from jobhunt.preferences import load_preferences
        try:
            with self.session_factory() as s:
                closed = auto_close(s, load_preferences(s), local_date(utcnow(), self.settings.tz), utcnow())
            logger.info("closed %d applications without a reply", closed)
        except Exception:
            logger.exception("auto-close failed")

    def push_digest(self) -> None:
        """The morning push (after the run nearest 10:00), only to an ntfy address the user set."""
        import requests
        from jobhunt.digest import build_digest, push_digest
        from jobhunt.preferences import is_paused, load_preferences
        try:
            now = utcnow()
            today = local_date(now, self.settings.tz)
            with self.session_factory() as s:
                prefs = load_preferences(s)
                if not prefs.ntfy_url or is_paused(prefs, today):
                    return
                digest = build_digest(s, prefs, now - timedelta(hours=24), now, today, self.settings.tz)
            if push_digest(prefs, digest, self.settings.public_url, self.post or requests.post):
                logger.info("digest pushed")
        except Exception:
            logger.exception("digest push failed")

    def _cron_jobs(self) -> list[tuple[Callable, CronTrigger, str, dict]]:
        """The jobs that run at local clock times, in the current time zone: (function, trigger, id, options)."""
        s = self.settings
        hours = run_hours(s.run_at, s.run_every_hours)
        push_at = datetime.combine(datetime.now(s.tz).date(), s.run_at) + timedelta(minutes=30)
        nightly = {"coalesce": True, "misfire_grace_time": 6 * 3600}
        jobs = [
            (self.trigger, CronTrigger(hour=",".join(map(str, hours)), minute=s.run_at.minute, timezone=s.tz),
             "scheduled", {"args": ["scheduled"], "coalesce": True, "misfire_grace_time": 3600}),
            (self.nightly_backup, CronTrigger(hour=3, minute=30, timezone=s.tz), "backup", nightly),
            (self.nightly_recheck, CronTrigger(hour=4, minute=0, timezone=s.tz), "recheck", nightly),
            (self.push_digest, CronTrigger(hour=push_at.hour, minute=push_at.minute, timezone=s.tz), "digest_push",
             {"coalesce": True, "misfire_grace_time": 3600}),
            (self.daily_auto_close, CronTrigger(hour=5, minute=15, timezone=s.tz), "auto_close", nightly),
        ]
        if self.fetch_page is not None:
            jobs.append((self.closed_check, CronTrigger(hour=5, minute=0, timezone=s.tz), "closed", nightly))
        return jobs

    def _add_cron_jobs(self) -> None:
        for func, trigger, job_id, options in self._cron_jobs():
            self._scheduler.add_job(func, trigger, id=job_id, replace_existing=True, **options)

    def set_time_zone(self, tz: ZoneInfo) -> None:
        """The user changed their time zone: every clock-time job moves to it at once, without a restart."""
        self.settings = replace(self.settings, tz=tz)
        if self._scheduler is not None:
            self._add_cron_jobs()
            logger.info("time zone changed to %s; jobs rescheduled", tz.key)

    def start(self) -> None:
        s = self.settings
        hours = run_hours(s.run_at, s.run_every_hours)
        self._scheduler = BackgroundScheduler(timezone=s.tz)
        self._add_cron_jobs()
        self._scheduler.add_job(self.catch_up_if_needed, "interval", minutes=CATCH_UP_CHECK_MINUTES,
                                id="catch_up_check", coalesce=True)
        if self.fetch_page is not None:
            self._scheduler.add_job(self.enrich_if_idle, "interval", minutes=ENRICH_EVERY_MINUTES, id="enrich",
                                    coalesce=True, max_instances=1, next_run_time=datetime.now(s.tz))
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
