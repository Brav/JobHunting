import json
from datetime import datetime

import pytest

from fakes import FakeTranslator, add_listing, add_run, job_id, make_posting as p
from jobhunt.actions import delete_listing
from jobhunt.job_listing import (clear_translation_errors, download_listing, translate_description,
                                 translate_title)
from jobhunt.models import Job
from jobhunt.runner import record_results
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError
from jobhunt.translate import NO_KEY_MESSAGE, TranslationError, TranslationQuotaExceeded

NOW = datetime(2026, 10, 28, 9, 0)
GERMAN = "Wir suchen einen erfahrenen PHP-Entwickler für unser Team in Berlin. Sie arbeiten mit Symfony."
ENGLISH = "We are looking for an experienced PHP developer to join our team in Berlin."


def seed(sf, *titles) -> list[int]:
    listing = add_listing(sf, "A")
    run = add_run(sf, NOW)
    with sf() as s:
        record_results(s, run, listing, [p(str(i), title=t) for i, t in enumerate(titles, start=1)], NOW)
    return [job_id(sf, str(i)) for i in range(1, len(titles) + 1)]


def job(sf, jid) -> Job:
    with sf() as s:
        return s.get(Job, jid)


def test_download_listing_stores_page_and_language(session_factory):
    [jid] = seed(session_factory, "Backend-Entwickler")
    download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, [("Seniority level", "Mid")]), NOW)
    j = job(session_factory, jid)
    assert (j.description, j.language, j.description_fetched_at, j.description_error) == (GERMAN, "de", NOW, None)
    assert json.loads(j.details) == [["Seniority level", "Mid"]]


def test_download_listing_failures(session_factory):
    a, b = seed(session_factory, "One", "Two")

    def gone(eid):
        raise SourceError("job page not available on LinkedIn")

    def blocked(eid):
        raise SourceBlocked("rate limited by LinkedIn")

    with pytest.raises(SourceError):
        download_listing(session_factory, a, gone, NOW)
    with pytest.raises(SourceBlocked):
        download_listing(session_factory, b, blocked, NOW)
    assert job(session_factory, a).description_error == "job page not available on LinkedIn"
    assert job(session_factory, b).description_error is None


def test_download_listing_for_deleted_job(session_factory):
    [jid] = seed(session_factory, "One")

    def fetch_then_delete(eid):
        with session_factory() as s:
            delete_listing(s, 1)
        return JobPage(ENGLISH, [])

    download_listing(session_factory, jid, fetch_then_delete, NOW)
    assert job(session_factory, jid) is None


def test_translate_title_only_for_non_english_once(session_factory):
    de, en, unknown = seed(session_factory, "Backend-Entwickler", "Backend Developer", "PHP")
    download_listing(session_factory, de, lambda eid: JobPage(GERMAN, []), NOW)
    download_listing(session_factory, en, lambda eid: JobPage(ENGLISH, []), NOW)
    translator = FakeTranslator()
    for jid in (de, en, unknown, de):
        translate_title(session_factory, jid, translator)
    assert translator.calls == [("Backend-Entwickler", "de")]
    assert job(session_factory, de).title_en == "EN:Backend-Entwickler"


def test_translate_title_errors(session_factory):
    a, b = seed(session_factory, "Entwickler A", "Entwickler B")
    for jid in (a, b):
        download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, []), NOW)
    translate_title(session_factory, a, FakeTranslator(fail=TranslationError(NO_KEY_MESSAGE)))
    with pytest.raises(TranslationQuotaExceeded):
        translate_title(session_factory, b, FakeTranslator(fail=TranslationQuotaExceeded("quota")))
    assert job(session_factory, a).translation_error == NO_KEY_MESSAGE
    assert job(session_factory, b).translation_error is None
    assert clear_translation_errors(session_factory, NO_KEY_MESSAGE) == 1
    assert job(session_factory, a).translation_error is None


def test_translate_description(session_factory):
    a, b = seed(session_factory, "Entwickler A", "Entwickler B")
    for jid in (a, b):
        download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, []), NOW)
    translate_description(session_factory, a, FakeTranslator())
    translate_description(session_factory, b, FakeTranslator(fail=TranslationQuotaExceeded("used up")))
    assert job(session_factory, a).description_en == f"EN:{GERMAN}"
    assert (job(session_factory, b).description_en, job(session_factory, b).translation_error) == (None, "used up")
