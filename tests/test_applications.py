from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select, update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.actions import delete_listing, set_job_status
from jobhunt.applications import (STAGE_LABELS, STAGES, StepError, add_step, delete_step, next_stage, steps_for)
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.models import Job, Listing
from jobhunt.runner import run_daily

TZ, NOW, TODAY = ZoneInfo("Europe/Belgrade"), datetime(2026, 10, 28, 9, 0), date(2026, 10, 28)


def make_jobs(sf, *ids):
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(NOW)
    run_daily(sf, FakeSource({"python": [[p(i) for i in ids]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    return [job_id(sf, i) for i in ids]


def apply(sf, jid, now=NOW):
    with sf() as s:
        return set_job_status(s, jid, "applied", now, TZ)


def add(sf, jid, stage, day, note=""):
    with sf() as s:
        add_step(s, jid, stage, day, note, TODAY, NOW)
        return s.get(Job, jid).stage


def stages(sf, jid):
    with sf() as s:
        return [(st.stage, st.on_date) for st in steps_for(s, jid)]


def test_stage_labels():
    assert STAGES == ("applied", "hr_interview", "technical_interview", "final_interview", "offer",
                      "rejected", "withdrawn", "no_response")
    assert [STAGE_LABELS[s] for s in STAGES] == ["Applied", "HR interview", "Technical interview", "Final interview",
                                                 "Offer", "Rejected", "Withdrawn", "No response"]


def test_applying_adds_one_applied_step_on_the_local_date(session_factory):
    a, b = make_jobs(session_factory, "1", "2")
    apply(session_factory, a)
    apply(session_factory, a)
    apply(session_factory, b, now=datetime(2026, 10, 27, 23, 30))  # 00:30 on the 28th in Belgrade
    assert stages(session_factory, a) == [("applied", TODAY)]
    assert stages(session_factory, b) == [("applied", TODAY)]
    with session_factory() as s:
        assert s.get(Job, a).stage == "applied"


def test_current_stage(session_factory):
    (j,) = make_jobs(session_factory, "1")
    apply(session_factory, j)
    assert add(session_factory, j, "hr_interview", date(2026, 10, 20)) == "hr_interview"  # before the Applied step
    assert add(session_factory, j, "technical_interview", date(2026, 10, 27)) == "technical_interview"
    assert add(session_factory, j, "rejected", date(2026, 10, 25)) == "technical_interview"  # earlier date
    assert add(session_factory, j, "offer", date(2026, 10, 27)) == "offer"  # same date, added later


def test_add_step_validation(session_factory):
    a, b = make_jobs(session_factory, "1", "2")
    apply(session_factory, a)
    cases = [(b, "hr_interview", TODAY, "", "Mark the job Applied first"),
             (a, "lunch", TODAY, "", "Unknown stage"),
             (a, "hr_interview", date(2026, 10, 29), "", "The date can't be in the future"),
             (a, "hr_interview", TODAY, "x" * 501, "Keep the note to 500 characters")]
    for jid, stage, day, note, message in cases:
        with session_factory() as s, pytest.raises(StepError, match=message):
            add_step(s, jid, stage, day, note, TODAY, NOW)
    with session_factory() as s, pytest.raises(LookupError):
        add_step(s, 9999, "offer", TODAY, "", TODAY, NOW)
    add(session_factory, a, "hr_interview", TODAY, note="  " + "x" * 500 + "  ")  # stripped to the limit: fine
    assert [st for st, _ in stages(session_factory, a)] == ["hr_interview", "applied"]
    assert stages(session_factory, b) == []


def test_delete_step(session_factory):
    a, b = make_jobs(session_factory, "1", "2")
    apply(session_factory, a)
    apply(session_factory, b)
    add(session_factory, a, "hr_interview", TODAY)
    with session_factory() as s:
        with pytest.raises(LookupError):
            delete_step(s, a, steps_for(s, b)[0].id)
        delete_step(s, a, steps_for(s, a)[0].id)
        assert s.get(Job, a).stage == "applied"
        delete_step(s, a, steps_for(s, a)[0].id)
        job = s.get(Job, a)
        assert (job.status, job.stage) == ("applied", None)


def test_leaving_applied_keeps_history(session_factory):
    (j,) = make_jobs(session_factory, "1")
    apply(session_factory, j)
    add(session_factory, j, "hr_interview", TODAY, note="call with Ana")
    with session_factory() as s:
        assert set_job_status(s, j, "hidden", NOW, TZ).stage is None
    assert [st for st, _ in stages(session_factory, j)] == ["hr_interview", "applied"]
    assert apply(session_factory, j, now=NOW + timedelta(days=3)).stage == "hr_interview"
    assert len(stages(session_factory, j)) == 2


def test_apply_then_undo_leaves_nothing_behind(session_factory):
    (j,) = make_jobs(session_factory, "1")
    apply(session_factory, j)
    with session_factory() as s:
        set_job_status(s, j, "new", NOW, TZ)
    assert stages(session_factory, j) == []
    apply(session_factory, j, now=datetime(2026, 11, 20, 9, 0))
    assert stages(session_factory, j) == [("applied", date(2026, 11, 20))]


def test_lone_applied_step_with_a_note_is_kept(session_factory):
    (j,) = make_jobs(session_factory, "1")
    apply(session_factory, j)
    with session_factory() as s:
        delete_step(s, j, steps_for(s, j)[0].id)
        add_step(s, j, "applied", date(2026, 10, 1), "via referral", TODAY, NOW)
        set_job_status(s, j, "interested", NOW, TZ)
    assert stages(session_factory, j) == [("applied", date(2026, 10, 1))]


def test_next_stage():
    assert [next_stage(s) for s in (None, "applied", "hr_interview", "final_interview", "offer", "rejected")] == \
        ["applied", "hr_interview", "technical_interview", "offer", "offer", "rejected"]


def test_backfill_gives_applied_jobs_an_applied_step(tmp_path):
    engine = make_engine(tmp_path / "b.db")
    init_db(engine, TZ)
    sf = make_session_factory(engine)
    (j,) = make_jobs(sf, "1")
    with sf() as s:  # marked Applied before steps existed
        s.exec(update(Job).where(Job.id == j).values(status="applied", status_changed_at=datetime(2026, 10, 27, 23, 30)))
        s.commit()
    with engine.begin() as conn:  # a database from before this version
        conn.exec_driver_sql("DROP TABLE application_steps")
    init_db(engine, TZ)
    init_db(engine, TZ)
    assert stages(sf, j) == [("applied", TODAY)]
    with sf() as s:
        assert s.get(Job, j).stage == "applied"


def test_delete_listing_keeps_jobs_with_history(session_factory):
    (j,) = make_jobs(session_factory, "1")
    apply(session_factory, j)
    add(session_factory, j, "hr_interview", TODAY)
    with session_factory() as s:
        set_job_status(s, j, "new", NOW, TZ)
        delete_listing(s, s.exec(select(Listing.id)).one())
        assert s.get(Job, j) is not None


def test_leaving_applied_later_keeps_the_applied_date(session_factory):
    (j,) = make_jobs(session_factory, "1")
    apply(session_factory, j, now=datetime(2026, 10, 1, 9, 0))
    with session_factory() as s:
        set_job_status(s, j, "hidden", NOW, TZ)  # X pressed by mistake weeks later
    apply(session_factory, j)  # ...and undone
    assert stages(session_factory, j) == [("applied", date(2026, 10, 1))]


def test_backfill_runs_once(tmp_path):
    engine = make_engine(tmp_path / "c.db")
    init_db(engine, TZ)
    sf = make_session_factory(engine)
    (j,) = make_jobs(sf, "1")
    apply(sf, j)
    with sf() as s:
        delete_step(s, j, steps_for(s, j)[0].id)  # the user removed it
    init_db(engine, TZ)  # next start
    assert stages(sf, j) == []
