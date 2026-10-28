"""One job's downloaded listing and its translations. Network calls happen outside database sessions."""

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import update

from jobhunt.language import detect_language
from jobhunt.models import Job
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError
from jobhunt.translate import TranslationError, TranslationQuotaExceeded, Translator


def _update(session_factory, job_id: int, **values) -> None:
    with session_factory() as s:
        s.exec(update(Job).where(Job.id == job_id).values(**values))
        s.commit()


def download_listing(session_factory, job_id: int, fetch_page: Callable[[str], JobPage], now: datetime) -> None:
    """Download and store the job's page. Raises SourceError; stores the reason unless LinkedIn blocked us."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        external_id = job.external_id
    try:
        page = fetch_page(external_id)
    except SourceBlocked:
        raise
    except SourceError as e:
        _update(session_factory, job_id, description_error=str(e))
        raise
    # A job deleted meanwhile simply updates no row.
    _update(session_factory, job_id, description=page.description, details=json.dumps(page.details),
            description_fetched_at=now, language=detect_language(page.description), description_error=None)


def _needs_translation(job: Job | None) -> bool:
    return job is not None and job.language is not None and job.language != "en"


def translate_title(session_factory, job_id: int, translator: Translator) -> None:
    """Translate a non-English title once. TranslationQuotaExceeded is raised (and not stored)."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if not _needs_translation(job) or job.title_en:
            return
        title, language = job.title, job.language
    try:
        title_en = translator.translate(title, language)
    except TranslationQuotaExceeded:
        raise
    except TranslationError as e:
        _update(session_factory, job_id, translation_error=str(e))
        return
    _update(session_factory, job_id, title_en=title_en, translation_error=None)


def translate_description(session_factory, job_id: int, translator: Translator) -> None:
    """Translate a non-English description once; any failure is stored, not raised."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if not _needs_translation(job) or not job.description or job.description_en:
            return
        description, language = job.description, job.language
    try:
        description_en = translator.translate(description, language)
    except TranslationError as e:
        _update(session_factory, job_id, translation_error=str(e))
        return
    _update(session_factory, job_id, description_en=description_en, translation_error=None)


def clear_translation_errors(session_factory, message: str) -> int:
    with session_factory() as s:
        result = s.exec(update(Job).where(Job.translation_error == message).values(translation_error=None))
        s.commit()
        return result.rowcount
