import random
from datetime import datetime

from sqlmodel import select

from fakes import FakeClock, FakeTranslator, add_listing, add_run, make_posting as p
from jobhunt.enrich import enrich_pending, first_of_next_month
from jobhunt.models import Job
from jobhunt.runner import record_results
from jobhunt.sources.base import JobPage, SourceBlocked, SourceError, SourceUnavailable
from jobhunt.translate import BAD_KEY_MESSAGE, TranslationError, TranslationQuotaExceeded, TranslationUnavailable

NOW = datetime(2026, 10, 28, 9, 0)
GERMAN = "Wir suchen einen erfahrenen PHP-Entwickler für unser Team in Berlin. Sie arbeiten mit Symfony."
ENGLISH = "We are looking for an experienced PHP developer to join our team in Berlin."


def seed(sf, n):
    listing, run = add_listing(sf, "A"), add_run(sf, NOW)
    with sf() as s:
        record_results(s, run, listing, [p(str(i)) for i in range(1, n + 1)], NOW)


class Pages:
    def __init__(self, texts=None, fail=None):
        self.texts, self.fail, self.calls = texts or {}, fail or {}, []

    def __call__(self, external_id):
        self.calls.append(external_id)
        if external_id in self.fail:
            raise self.fail[external_id]
        return JobPage(self.texts.get(external_id, ENGLISH), [])


def run(sf, pages, translator=None, **kw):
    clock = FakeClock(NOW)
    kw.setdefault("sleep", clock.sleep)
    result = enrich_pending(sf, pages, translator or FakeTranslator(), clock=clock, rng=random.Random(0), **kw)
    return result, clock


def test_downloads_newest_first_up_to_batch_with_pauses(session_factory):
    seed(session_factory, 4)
    pages = Pages()
    result, clock = run(session_factory, pages, batch=3)
    assert pages.calls == ["4", "3", "2"] and result.downloaded == 3
    assert len(clock.sleeps) == 2 and all(10 <= s <= 20 for s in clock.sleeps)


def test_stops_at_a_block_and_keeps_failures(session_factory):
    seed(session_factory, 3)
    pages = Pages(fail={"3": SourceError("job page not available on LinkedIn"), "2": SourceBlocked("rate limited")})
    result, _ = run(session_factory, pages)
    assert (result.failed, result.blocked, result.downloaded) == (1, True, 0) and pages.calls == ["3", "2"]
    pages2 = Pages()
    run(session_factory, pages2)
    assert pages2.calls == ["2", "1"]  # "3" failed for good; "2" was only blocked


def test_stops_when_a_search_starts(session_factory):
    seed(session_factory, 3)
    pages = Pages()
    result, _ = run(session_factory, pages, should_stop=lambda: len(pages.calls) >= 1)
    assert pages.calls == ["3"] and result.downloaded == 1


def test_translates_non_english_titles_once(session_factory):
    seed(session_factory, 2)
    translator = FakeTranslator()
    result, _ = run(session_factory, Pages(texts={"1": GERMAN}), translator)
    run(session_factory, Pages(), translator)
    assert translator.calls == [("Engineer 1", "de")] and result.titles == 1


def test_quota_stops_title_translation(session_factory):
    seed(session_factory, 2)
    result, _ = run(session_factory, Pages(texts={"1": GERMAN, "2": GERMAN}),
                    FakeTranslator(fail=TranslationQuotaExceeded("quota")))
    assert result.quota_exceeded and result.titles == 0
    with session_factory() as s:
        assert all(j.translation_error is None for j in s.exec(select(Job)).all())


def test_first_of_next_month():
    assert first_of_next_month(datetime(2026, 10, 28, 9)) == datetime(2026, 11, 1)
    assert first_of_next_month(datetime(2026, 12, 31, 23)) == datetime(2027, 1, 1)


def test_temporary_error_stops_the_pass_without_storing(session_factory):
    seed(session_factory, 2)
    pages = Pages(fail={"2": SourceUnavailable("LinkedIn returned status 502")})
    result, _ = run(session_factory, pages)
    assert pages.calls == ["2"] and (result.failed, result.blocked) == (0, False)
    pages2 = Pages()
    run(session_factory, pages2)
    assert pages2.calls == ["2", "1"]


def test_unavailable_translation_is_retried_later(session_factory):
    seed(session_factory, 2)
    bad_key = FakeTranslator(fail=TranslationUnavailable(BAD_KEY_MESSAGE))
    result, _ = run(session_factory, Pages(texts={"1": GERMAN, "2": GERMAN}), bad_key)
    assert result.titles == 0 and len(bad_key.calls) == 1
    fixed = FakeTranslator()
    result, _ = run(session_factory, Pages(), fixed)
    assert result.titles == 2


def test_title_count_excludes_failures(session_factory):
    seed(session_factory, 1)
    result, _ = run(session_factory, Pages(texts={"1": GERMAN}),
                    FakeTranslator(fail=TranslationError("DeepL can't translate German")))
    assert result.titles == 0


def test_stops_when_a_search_starts_during_the_pause(session_factory):
    seed(session_factory, 3)
    pages, search = Pages(), []
    result, _ = run(session_factory, pages, should_stop=lambda: bool(search), sleep=lambda s: search.append(1))
    assert pages.calls == ["3"] and result.downloaded == 1


def test_an_unexpected_error_is_stored_and_the_pass_goes_on(session_factory):
    seed(session_factory, 2)
    pages = Pages(fail={"2": ValueError("unexpected markup")})
    result, _ = run(session_factory, pages)
    assert pages.calls == ["2", "1"] and (result.failed, result.downloaded) == (1, 1)
    with session_factory() as s:
        errors = {j.external_id: j.description_error for j in s.exec(select(Job))}
    assert errors == {"1": None, "2": "ValueError: unexpected markup"}
