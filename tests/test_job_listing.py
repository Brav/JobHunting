import json
from datetime import date, datetime

import pytest

from fakes import FakeTranslator, add_listing, add_run, job_id, make_posting as p
from jobhunt.actions import delete_listing
from jobhunt.job_listing import (clear_service_translation_errors, download_listing, translate_description,
                                 translate_title)
from jobhunt.models import Job
from jobhunt.runner import record_results
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError, SourceUnavailable
from jobhunt.translate import (BAD_KEY_MESSAGE, NO_KEY_MESSAGE, TranslationError, TranslationQuotaExceeded,
                               TranslationUnavailable)

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
    a, b, c = seed(session_factory, "One", "Two", "Three")

    def fail(error):
        def fetch(eid):
            raise error
        return fetch

    with pytest.raises(SourceError):
        download_listing(session_factory, a, fail(SourceError("job page not available on LinkedIn")), NOW)
    with pytest.raises(SourceBlocked):
        download_listing(session_factory, b, fail(SourceBlocked("rate limited by LinkedIn")), NOW)
    with pytest.raises(SourceUnavailable):
        download_listing(session_factory, c, fail(SourceUnavailable("LinkedIn returned status 502")), NOW)
    assert job(session_factory, a).description_error == "job page not available on LinkedIn"
    assert job(session_factory, b).description_error is None and job(session_factory, c).description_error is None


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
    assert [translate_title(session_factory, jid, translator) for jid in (de, en, unknown, de)] == [
        True, False, False, False]
    assert translator.calls == [("Backend-Entwickler", "de")]
    assert job(session_factory, de).title_en == "EN:Backend-Entwickler"


def test_translate_title_stores_only_text_errors(session_factory):
    a, b, c = seed(session_factory, "Entwickler A", "Entwickler B", "Entwickler C")
    for jid in (a, b, c):
        download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, []), NOW)
    assert translate_title(session_factory, a, FakeTranslator(fail=TranslationError("DeepL can't translate German"))) is False
    with pytest.raises(TranslationUnavailable):
        translate_title(session_factory, b, FakeTranslator(fail=TranslationUnavailable(BAD_KEY_MESSAGE)))
    with pytest.raises(TranslationQuotaExceeded):
        translate_title(session_factory, c, FakeTranslator(fail=TranslationQuotaExceeded("quota")))
    assert job(session_factory, a).translation_error == "DeepL can't translate German"
    assert job(session_factory, b).translation_error is None and job(session_factory, c).translation_error is None


def test_translate_description(session_factory):
    a, b, c = seed(session_factory, "Entwickler A", "Entwickler B", "Entwickler C")
    for jid in (a, b, c):
        download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, []), NOW)
    assert translate_description(session_factory, a, FakeTranslator()) is None
    assert translate_description(session_factory, b, FakeTranslator(fail=TranslationQuotaExceeded("used up"))) == "used up"
    assert translate_description(session_factory, c, FakeTranslator(fail=TranslationError("DeepL can't translate German"))) == "DeepL can't translate German"
    assert job(session_factory, a).description_en == f"EN:{GERMAN}"
    assert (job(session_factory, b).description_en, job(session_factory, b).translation_error) == (None, None)
    assert job(session_factory, c).translation_error == "DeepL can't translate German"


def test_clear_service_translation_errors(session_factory):
    a, b = seed(session_factory, "One", "Two")
    with session_factory() as s:
        s.get(Job, a).translation_error = NO_KEY_MESSAGE
        s.get(Job, b).translation_error = "DeepL can't translate Serbian/Croatian"
        s.commit()
    assert clear_service_translation_errors(session_factory) == 1
    assert (job(session_factory, a).translation_error, job(session_factory, b).translation_error) == (
        None, "DeepL can't translate Serbian/Croatian")


def repost(sf, external_id: str, title: str) -> None:
    """What a search does when it finds a newer repost (or a retitled posting) of a job."""
    with sf() as s:
        record_results(s, add_run(sf, NOW), 1, [p(external_id, title=title, posted_at=date(2026, 10, 27))], NOW)


def test_download_listing_drops_a_page_reposted_meanwhile(session_factory):
    [jid] = seed(session_factory, "PHP Developer")

    def fetch_during_repost(eid):
        repost(session_factory, "9", "PHP Developer")
        return JobPage(ENGLISH, [])

    download_listing(session_factory, jid, fetch_during_repost, NOW)
    j = job(session_factory, jid)
    assert (j.external_id, j.description, j.language) == ("9", None, None)


def test_download_listing_drops_an_error_about_a_posting_reposted_meanwhile(session_factory):
    [jid] = seed(session_factory, "PHP Developer")

    def fail_during_repost(eid):
        repost(session_factory, "9", "PHP Developer")
        raise SourceError("job page not available on LinkedIn")

    with pytest.raises(SourceError):
        download_listing(session_factory, jid, fail_during_repost, NOW)
    assert job(session_factory, jid).description_error is None


def test_translate_description_drops_a_translation_of_replaced_text(session_factory):
    [jid] = seed(session_factory, "Entwickler")
    download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, []), NOW)

    class ReplacingTranslator(FakeTranslator):
        def translate(self, text, language):
            download_listing(session_factory, jid, lambda eid: JobPage(GERMAN + " Neu.", []), NOW)
            return super().translate(text, language)

    assert translate_description(session_factory, jid, ReplacingTranslator()) is None
    assert job(session_factory, jid).description_en is None


def test_translate_title_drops_a_translation_of_a_replaced_title(session_factory):
    [jid] = seed(session_factory, "Entwickler")
    download_listing(session_factory, jid, lambda eid: JobPage(GERMAN, []), NOW)

    class RetitlingTranslator(FakeTranslator):
        def translate(self, text, language):
            repost(session_factory, "1", "Senior Entwickler")
            return super().translate(text, language)

    translate_title(session_factory, jid, RetitlingTranslator())
    assert job(session_factory, jid).title_en is None
