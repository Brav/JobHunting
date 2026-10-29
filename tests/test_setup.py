import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import select

from jobhunt import checks
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.eligibility import (DEFAULT_PROFILE, LEGACY_PROFILE, SETUP_REASON, Profile, load_profile,
                                 profile_saved, save_profile)
from jobhunt.models import Job, Listing
from jobhunt.setup import adopt_legacy_profile, setup_state, skip_setup, time_zone

NOW = datetime(2026, 10, 29, 9, 0)


def add_job(s, **kw):
    job = Job(external_id=kw.pop("external_id", "1"), match_key="t|c", first_seen_at=NOW, title=kw.pop("title", "Engineer"),
              company="Acme", location=kw.pop("location", "Berlin, Germany"), url="https://example.com/1", **kw)
    s.add(job)
    s.commit()
    return job


def test_neutral_default_profile():
    assert DEFAULT_PROFILE == Profile(work_rights=(), home_country="", time_zone="UTC", relocate_to=(),
                                      languages=("en",), contracts=("employment", "b2b", "eor"))


def test_legacy_profile_keeps_the_old_default():
    assert LEGACY_PROFILE == Profile(work_rights=("EU",), home_country="RS", time_zone="Europe/Belgrade",
                                     relocate_to=("EU",), languages=("en", "sr", "hr", "bs", "me"),
                                     contracts=("employment", "b2b", "eor"))


def test_database_with_jobs_adopts_the_legacy_profile(fresh_session_factory):
    with fresh_session_factory() as s:
        add_job(s)
        assert adopt_legacy_profile(s, NOW) is True
    with fresh_session_factory() as s:
        assert load_profile(s) == LEGACY_PROFILE
        assert setup_state(s) == "done"


def test_database_with_only_a_listing_adopts_it_too(fresh_session_factory):
    with fresh_session_factory() as s:
        s.add(Listing(name="A", keywords="python", location="Berlin"))
        s.commit()
        assert adopt_legacy_profile(s, NOW) is True
        assert load_profile(s) == LEGACY_PROFILE


def test_new_database_stays_pending(fresh_session_factory):
    with fresh_session_factory() as s:
        assert adopt_legacy_profile(s, NOW) is False
        assert profile_saved(s) is False
        assert setup_state(s) == "pending"


def test_a_saved_profile_is_never_replaced(fresh_session_factory):
    other = replace(DEFAULT_PROFILE, home_country="DE", languages=("en", "de"))
    with fresh_session_factory() as s:
        save_profile(s, other, NOW)
        add_job(s)
        assert adopt_legacy_profile(s, NOW) is False
        assert load_profile(s) == other


def test_skip_setup(fresh_session_factory):
    with fresh_session_factory() as s:
        skip_setup(s, NOW)
    with fresh_session_factory() as s:
        assert setup_state(s) == "skipped"


def test_jobs_are_check_until_a_profile_is_saved(fresh_session_factory):
    with fresh_session_factory() as s:
        job = add_job(s, location="London, England, UK", workplace="remote")
        checks.refresh(s, job, checks.load_context(s))
        assert job.eligibility == "check"
        assert json.loads(job.eligibility_reasons) == [SETUP_REASON]
        save_profile(s, LEGACY_PROFILE, NOW)
        checks.refresh(s, job, checks.load_context(s))
        assert job.eligibility == "not_eligible"


def test_time_zone_from_the_saved_profile_else_the_fallback(fresh_session_factory):
    with fresh_session_factory() as s:
        assert time_zone(s, ZoneInfo("UTC")) == ZoneInfo("UTC")
        save_profile(s, replace(DEFAULT_PROFILE, time_zone="Asia/Tokyo"), NOW)
        assert time_zone(s, ZoneInfo("UTC")) == ZoneInfo("Asia/Tokyo")


def test_app_start_adopts_the_legacy_profile(tmp_path, app_factory):
    engine = make_engine(tmp_path / "web.db")
    init_db(engine)
    with make_session_factory(engine)() as s:
        add_job(s)
    engine.dispose()
    app = app_factory(setup_done=False)
    with app.state.session_factory() as s:
        assert profile_saved(s) is True
        assert load_profile(s) == LEGACY_PROFILE
        assert len(s.exec(select(Job)).all()) == 1
