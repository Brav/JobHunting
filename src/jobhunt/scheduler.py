"""When runs happen: a check every minute searches the listings whose time has come (each at its own times, or the
default schedule's), missed times included; or on demand. One run at a time."""

import logging
import threading
import time as _time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from jobhunt.models import Listing, Run, RunListing
from jobhunt.preferences import Preferences, is_paused, load_preferences
from jobhunt.runner import run_daily
from jobhunt.schedule import Schedule, describe, from_env, latest_slot, next_slot, parse, push_time
from jobhunt.sources.base import JobPage, JobSource
from jobhunt.timeutil import local_date, utcnow
from jobhunt.translate import Translator

logger = logging.getLogger(__name__)


DUE_CHECK_MINUTES = 1


def default_schedule(prefs: Preferences, settings: Settings) -> Schedule:
    """The schedule of every listing without its own: Settings', else JOBHUNT_RUN_AT every JOBHUNT_RUN_EVERY_HOURS."""
    return parse(prefs.schedule) or from_env(settings.run_at, settings.run_every_hours)


def changed_at(text: str) -> datetime | None:
    """When the default schedule last changed (naive UTC); unreadable text counts as never, so searching goes on."""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("unreadable default schedule change time %r; ignoring it", text)
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def due_listings(session: Session, now: datetime, tz: ZoneInfo, default: Schedule,
                 default_changed_at: datetime | None) -> list[int]:
    """Active listings whose latest scheduled time has passed since they last searched (or were created, or their
    schedule changed): the ones to search now. A missed time is caught up once, never once per missed slot."""
    attempts = dict(session.exec(
        select(RunListing.listing_id, func.max(Run.started_at)).join(Run, Run.id == RunListing.run_id)
        .where(or_(Run.error.is_(None), Run.error != "interrupted")).group_by(RunListing.listing_id)).all())
    due = []
    for listing in session.exec(select(Listing).where(Listing.active == True).order_by(Listing.id)):  # noqa: E712
        own = parse(listing.schedule)
        if listing.schedule and own is None:
            logger.warning("listing %r has an unreadable schedule %r; using the default", listing.name, listing.schedule)
        marks = [attempts.get(listing.id), listing.created_at, listing.schedule_changed_at,
                 default_changed_at if own is None else None]
        if latest_slot(own or default, now, tz) > max(m for m in marks if m is not None):
            due.append(listing.id)
    return due


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

    def _run(self, trigger: str, listing_ids: list[int] | None = None) -> int:
        return run_daily(self.session_factory, self.source, trigger=trigger,
                         results_per_listing=self.settings.results_per_listing,
                         clock=self.clock, sleep=self.sleep, listing_ids=listing_ids)

    def run_blocking(self, trigger: str) -> int | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._run(trigger)
        finally:
            self._lock.release()

    def trigger(self, trigger: str, listing_ids: list[int] | None = None) -> bool:
        """Start a run in the background (every active listing, or only these); False when it can't start now."""
        if trigger != "manual" and self._paused():
            logger.info("searches are paused; %s run skipped", trigger)
            return False
        if not self._has_listings():  # a new install: nothing to search, and no empty "0 of 0" run on the report
            logger.info("no active listings yet; %s run skipped", trigger)
            return False
        if not self._lock.acquire(blocking=False):
            logger.info("a run is already in progress; %s trigger ignored", trigger)
            return False

        def work():
            try:
                self._run(trigger, listing_ids)
            finally:
                self._lock.release()

        threading.Thread(target=work, name=f"jobhunt-run-{trigger}", daemon=True).start()
        return True

    def _has_listings(self) -> bool:
        with self.session_factory() as s:
            return s.exec(select(Listing.id).where(Listing.active == True).limit(1)).first() is not None  # noqa: E712

    def _paused(self) -> bool:
        with self.session_factory() as s:
            return is_paused(load_preferences(s), local_date(utcnow(), self.settings.tz))

    def _default(self, session: Session) -> tuple[Schedule, datetime | None]:
        prefs = load_preferences(session)
        return default_schedule(prefs, self.settings), changed_at(prefs.schedule_changed_at)

    def check_due(self) -> bool:
        """Every minute: search the listings whose time has come (missed times included), together in one run."""
        if self.is_running or self._paused():
            return False
        with self.session_factory() as s:
            due = due_listings(s, self.clock(), self.settings.tz, *self._default(s))
        if not due:
            return False
        logger.info("listings due: %s", ", ".join(map(str, due)))
        return self.trigger("scheduled", due)

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
        try:
            with self.session_factory() as s:
                closed = auto_close(s, load_preferences(s), local_date(utcnow(), self.settings.tz), utcnow())
            logger.info("closed %d applications without a reply", closed)
        except Exception:
            logger.exception("auto-close failed")

    def push_digest(self) -> None:
        """The daily push (half an hour after the default schedule starts), only to an ntfy address the user set."""
        import requests
        from jobhunt.digest import build_digest, push_digest
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
        with self.session_factory() as session:
            push_at = push_time(self._default(session)[0])  # half an hour after the default schedule's start
        nightly = {"coalesce": True, "misfire_grace_time": 6 * 3600}
        jobs = [
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

    def schedule_changed(self) -> None:
        """The default schedule changed in Settings: the phone push follows its new start."""
        if self._scheduler is not None:
            self._add_cron_jobs()

    def set_time_zone(self, tz: ZoneInfo) -> None:
        """The user changed their time zone: every clock-time job moves to it at once, without a restart."""
        self.settings = replace(self.settings, tz=tz)
        if self._scheduler is not None:
            self._add_cron_jobs()
            logger.info("time zone changed to %s; jobs rescheduled", tz.key)

    def start(self) -> None:
        s = self.settings
        self._scheduler = BackgroundScheduler(timezone=s.tz)
        self._add_cron_jobs()
        self._scheduler.add_job(self.check_due, "interval", minutes=DUE_CHECK_MINUTES, id="due_check",
                                coalesce=True, max_instances=1)
        if self.fetch_page is not None:
            self._scheduler.add_job(self.enrich_if_idle, "interval", minutes=ENRICH_EVERY_MINUTES, id="enrich",
                                    coalesce=True, max_instances=1, next_run_time=datetime.now(s.tz))
        self._scheduler.start()
        with self.session_factory() as session:
            default_text = describe(self._default(session)[0])
        logger.info("scheduler started; listings run at their own times (default: %s), %s", default_text, s.tz.key)
        self.check_due()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def next_run_at(self) -> datetime | None:
        """The earliest next scheduled time of any active listing, in the user's time zone; None without listings."""
        now, tz = self.clock(), self.settings.tz
        with self.session_factory() as s:
            default, _ = self._default(s)
            schedules = [parse(text) or default for text in s.exec(
                select(Listing.schedule).where(Listing.active == True)).all()]  # noqa: E712
        if not schedules:
            return None
        return min(next_slot(sched, now, tz) for sched in schedules).replace(tzinfo=UTC).astimezone(tz)
