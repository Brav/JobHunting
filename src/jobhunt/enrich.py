"""The background pass: download jobs' full listings (paced) and translate non-English titles."""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import select

from jobhunt.job_listing import download_listing, translate_title
from jobhunt.models import Job
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError, SourceUnavailable
from jobhunt.timeutil import utcnow
from jobhunt.translate import TranslationQuotaExceeded, TranslationUnavailable, Translator

ENRICH_EVERY_MINUTES = 5
ENRICH_BATCH = 15
ENRICH_PAUSE_SECONDS = (10, 20)
BLOCK_BACKOFF = timedelta(minutes=30)

logger = logging.getLogger(__name__)


@dataclass
class EnrichResult:
    downloaded: int = 0
    failed: int = 0
    blocked: bool = False
    titles: int = 0
    quota_exceeded: bool = False


def first_of_next_month(now: datetime) -> datetime:
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)


def enrich_pending(session_factory, fetch_page: Callable[[str], JobPage], translator: Translator, *,
                   should_stop: Callable[[], bool] = lambda: False, translate_titles: bool = True,
                   batch: int = ENRICH_BATCH, clock: Callable[[], datetime] = utcnow,
                   sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> EnrichResult:
    rng = rng or random.Random()
    result = EnrichResult()
    with session_factory() as s:
        pending = s.exec(select(Job.id)
                         .where(Job.description.is_(None), Job.description_error.is_(None))
                         .order_by(Job.first_seen_at.desc(), Job.id.desc())
                         .limit(batch)).all()
    for i, job_id in enumerate(pending):
        if should_stop():
            break
        if i:
            sleep(rng.uniform(*ENRICH_PAUSE_SECONDS))
            if should_stop():
                break  # a search started during the pause
        try:
            download_listing(session_factory, job_id, fetch_page, clock())
        except SourceBlocked:
            result.blocked = True
            break
        except SourceUnavailable:
            break  # network or LinkedIn trouble: the next pass tries again
        except SourceError:
            result.failed += 1
            continue
        except Exception as e:
            # A bug or an odd page: remember it on this job (not retried) rather than end the whole pass.
            logger.exception("downloading job %d failed", job_id)
            with session_factory() as s:
                s.exec(update(Job).where(Job.id == job_id).values(description_error=f"{type(e).__name__}: {e}"))
                s.commit()
            result.failed += 1
            continue
        result.downloaded += 1

    if translate_titles:
        with session_factory() as s:
            untranslated = s.exec(select(Job.id).where(Job.language.is_not(None), Job.language != "en",
                                                       Job.title_en.is_(None), Job.translation_error.is_(None))).all()
        for job_id in untranslated:
            try:
                result.titles += translate_title(session_factory, job_id, translator)
            except TranslationQuotaExceeded:
                result.quota_exceeded = True
                break
            except TranslationUnavailable:
                break  # no key, bad key or DeepL down: the next pass tries again
    return result
