import json
from dataclasses import replace
from datetime import datetime

from sqlmodel import select, update

import jobhunt.eligibility as eligibility
from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.db import init_db
from jobhunt.eligibility import DEFAULT_PROFILE, effective, load_profile, refresh_all, refresh_job, save_profile
from jobhunt.job_listing import download_listing, translate_description
from jobhunt.models import Job
from jobhunt.runner import run_daily
from jobhunt.sources.base import JobPage

NOW = datetime(2026, 10, 28, 9, 0)


def found(sf, *postings):
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(sf, FakeSource({"python": [list(postings)]}), trigger="manual", clock=clock, sleep=clock.sleep)


def get(sf, external_id):
    with sf() as s:
        return s.exec(select(Job).where(Job.external_id == external_id)).one()


class ToPortugal:
    """Translates anything into an English residence rule."""

    def translate(self, text, language):
        return "You must be based in Portugal."


def test_profile_defaults_and_saves(session_factory):
    with session_factory() as s:
        assert load_profile(s) == DEFAULT_PROFILE
        save_profile(s, replace(DEFAULT_PROFILE, languages=("en", "de")), NOW)
    with session_factory() as s:
        assert load_profile(s).languages == ("en", "de")


def test_search_results_are_checked(session_factory):
    found(session_factory, p("1", location="London, England, UK", is_remote=True), p("2", location="Portugal", is_remote=True))
    one, two = get(session_factory, "1"), get(session_factory, "2")
    assert (one.eligibility, json.loads(one.eligibility_reasons)[0]["text"]) == \
        ("not_eligible", "Job in the UK — no work rights there")
    assert (two.eligibility, two.eligibility_rules) == ("check", eligibility.RULES_VERSION)


def test_download_rechecks(session_factory):
    found(session_factory, p("1", location="Portugal", is_remote=True))
    download_listing(session_factory, job_id(session_factory, "1"),
                     lambda _: JobPage("100% Remote | Must be based in Portugal", []), NOW)
    assert get(session_factory, "1").eligibility == "relocate"


def test_original_language_rule_before_translation(session_factory):
    found(session_factory, p("1", location="Germany", is_remote=True))
    download_listing(session_factory, job_id(session_factory, "1"), lambda _: JobPage(
        "Wir suchen Verstärkung für unser Team in Berlin. Du sprichst fließend Deutsch.", []), NOW)
    assert get(session_factory, "1").eligibility == "not_eligible"


def test_translation_rechecks(session_factory):
    found(session_factory, p("1", location="Germany", is_remote=True))
    jid = job_id(session_factory, "1")
    download_listing(session_factory, jid, lambda _: JobPage(
        "Wir suchen Verstärkung für unser Team in Berlin und freuen uns auf deine Bewerbung.", []), NOW)
    assert get(session_factory, "1").eligibility == "check"
    translate_description(session_factory, jid, ToPortugal())
    assert get(session_factory, "1").eligibility == "relocate"


def test_override_survives_refresh(session_factory):
    found(session_factory, p("1", location="UK", is_remote=True))
    with session_factory() as s:
        s.exec(update(Job).values(eligibility_override="eligible"))
        s.commit()
    refresh_job(session_factory, job_id(session_factory, "1"))
    with session_factory() as s:
        refresh_all(s, DEFAULT_PROFILE)
    job = get(session_factory, "1")
    assert (job.eligibility, job.eligibility_override, effective(job)) == ("not_eligible", "eligible", "eligible")


def test_startup_checks_stale_jobs(engine, session_factory, monkeypatch):
    found(session_factory, p("1", location="UK", is_remote=True), p("2", location="Spain", is_remote=True))
    with session_factory() as s:
        s.exec(update(Job).where(Job.external_id == "1").values(eligibility=None, eligibility_rules=None))
        s.commit()
    init_db(engine)
    assert get(session_factory, "1").eligibility == "not_eligible"
    monkeypatch.setattr(eligibility, "RULES_VERSION", eligibility.RULES_VERSION + 1)
    with session_factory() as s:
        assert refresh_all(s, DEFAULT_PROFILE, stale_only=True) == 2
        assert refresh_all(s, DEFAULT_PROFILE, stale_only=True) == 0
