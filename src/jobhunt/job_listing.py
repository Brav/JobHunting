"""One job's downloaded listing and its translations. Network calls happen outside database sessions."""

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import update

from jobhunt.language import detect_language
from jobhunt.checks import refresh_job
from jobhunt.signals import applicant_count
from jobhunt.workplace import details_value, workplace_from
from jobhunt.models import Job
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError, SourceUnavailable
from jobhunt.translate import TranslationError, TranslationUnavailable, Translator


def _update(session_factory, job_id: int, *conditions, **values) -> None:
    """Update the job, but only while `conditions` hold: a search may repost or retitle it meanwhile."""
    with session_factory() as s:
        s.exec(update(Job).where(Job.id == job_id, *conditions).values(**values))
        s.commit()


def download_listing(session_factory, job_id: int, fetch_page: Callable[[str], JobPage], now: datetime) -> None:
    """Download and store the job's page. Raises SourceError; stores the reason only if it's about this job."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        external_id, title, location, workplace = job.external_id, job.title, job.location, job.workplace
    same_posting = Job.external_id == external_id
    try:
        page = fetch_page(external_id)
    except (SourceBlocked, SourceUnavailable):
        raise  # LinkedIn or the network, not this job: try again later
    except SourceError as e:
        _update(session_factory, job_id, same_posting, description_error=str(e))
        raise
    # A job deleted or reposted meanwhile simply updates no row.
    details_values = ", ".join(value for _, value in page.details)
    _update(session_factory, job_id, same_posting, description=page.description, details=json.dumps(page.details),
            description_fetched_at=now, language=detect_language(page.description), description_error=None,
            seniority=details_value(page.details, "Seniority level"),
            employment_type=details_value(page.details, "Employment type"),
            workplace=workplace_from(title, location, details_values, page.description) or workplace,
            applicants_text=page.applicants_text, applicants=applicant_count(page.applicants_text),
            closed_at=now if page.closed else None, apply_url=page.apply_url, salary_source_text=page.salary_text)
    refresh_job(session_factory, job_id)


def _needs_translation(job: Job | None) -> bool:
    return job is not None and job.language is not None and job.language != "en"


def translate_title(session_factory, job_id: int, translator: Translator) -> bool:
    """Translate a non-English title once; True if translated. TranslationUnavailable is raised, not stored."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if not _needs_translation(job) or job.title_en:
            return False
        title, language = job.title, job.language
    try:
        title_en = translator.translate(title, language)
    except TranslationUnavailable:
        raise
    except TranslationError as e:
        _update(session_factory, job_id, Job.title == title, translation_error=str(e))
        return False
    _update(session_factory, job_id, Job.title == title, title_en=title_en, translation_error=None)
    return True


def translate_description(session_factory, job_id: int, translator: Translator) -> str | None:
    """Translate a non-English description once. Returns why it failed, storing only reasons about the text."""
    with session_factory() as s:
        job = s.get(Job, job_id)
        if not _needs_translation(job) or not job.description or job.description_en:
            return None
        description, language = job.description, job.language
    try:
        description_en = translator.translate(description, language)
    except TranslationUnavailable as e:
        return str(e)
    except TranslationError as e:
        _update(session_factory, job_id, Job.description == description, translation_error=str(e))
        return str(e)
    _update(session_factory, job_id, Job.description == description, description_en=description_en,
            translation_error=None)
    refresh_job(session_factory, job_id)
    return None


def clear_service_translation_errors(session_factory) -> int:
    """Forget stored errors that weren't about the text (older versions stored service problems too)."""
    with session_factory() as s:
        result = s.exec(update(Job).where(Job.translation_error.is_not(None),
                                          Job.translation_error.not_like("DeepL can't translate %"))
                        .values(translation_error=None))
        s.commit()
        return result.rowcount
