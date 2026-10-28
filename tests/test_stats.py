from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlmodel import select, update

from fakes import FakeClock, FakeSource, add_listing, make_posting as p
from jobhunt.applications import refresh_stage
from jobhunt.models import ApplicationStep, Job, Listing
from jobhunt.runner import run_daily
from jobhunt.stats import (activity, application_rows, breakdowns, load_applications, overview, pipeline)

TZ, NOW, TODAY = ZoneInfo("Europe/Belgrade"), datetime(2026, 10, 28, 9, 0), date(2026, 10, 28)


def D(day, month=10):
    return date(2026, month, day)


def found(sf, outcomes, when=NOW, listings=(("A", "python"),)):
    with sf() as s:
        existing = set(s.exec(select(Listing.name)).all())
    for name, keywords in listings:
        if name not in existing:
            add_listing(sf, name, keywords=keywords)
    clock = FakeClock(when)
    run_daily(sf, FakeSource(outcomes), trigger="manual", clock=clock, sleep=clock.sleep)


def set_job(sf, external_id, **values):
    with sf() as s:
        s.exec(update(Job).where(Job.external_id == external_id).values(**values))
        s.commit()


def application(sf, external_id, *steps, company=None):
    with sf() as s:
        job = s.exec(select(Job).where(Job.external_id == external_id)).one()
        job.status, job.status_changed_at = "applied", NOW
        job.company = company or job.company
        for stage, day in steps:
            s.add(ApplicationStep(job_id=job.id, stage=stage, on_date=day))
        s.flush()
        refresh_stage(s, job)
        s.commit()
        return job.id


def apps(sf):
    with sf() as s:
        return load_applications(s)


def test_overview(session_factory):
    sf = session_factory
    found(sf, {"python": [[p(str(i)) for i in range(1, 6)]]}, when=datetime(2026, 10, 20, 9, 0))
    found(sf, {"python": [[p("6"), p("7")]]})
    set_job(sf, "1", status="interested", status_changed_at=NOW)
    set_job(sf, "2", status="hidden", status_changed_at=datetime(2026, 10, 21, 9, 0))
    set_job(sf, "3", status="hidden", status_changed_at=NOW)
    application(sf, "4", ("applied", D(22)))
    application(sf, "6", ("applied", TODAY))
    with sf() as s:
        assert [(t.label, t.total, t.this_week) for t in overview(s, TODAY, TZ)] == [
            ("Found", 7, 2), ("Undecided", 2, 1), ("Saved", 1, 1), ("Applied", 2, 1), ("Hidden", 2, 1)]


def test_application_rows(session_factory):
    sf = session_factory
    found(sf, {"python": [[p(str(i)) for i in range(1, 6)]]})
    a = application(sf, "1", ("applied", D(1)), ("hr_interview", D(14)))   # waiting 14: flagged
    b = application(sf, "2", ("applied", D(15)), company="Zeta")           # waiting 13
    c = application(sf, "3", ("applied", D(2)), ("rejected", D(10)))       # closed
    d = application(sf, "4")                                                # no steps
    e = application(sf, "5", ("applied", D(15)), company="Alpha")          # waiting 13, sorts before Zeta
    rows, counts = application_rows(apps(sf), TODAY, "open")
    assert counts == {"open": 4, "closed": 1, "all": 5}
    assert [(r.app.job.id, r.waiting, r.stale) for r in rows] == [(a, 14, True), (e, 13, False), (b, 13, False),
                                                                   (d, None, False)]
    assert [(r.app.job.id, r.waiting, r.stale) for r in application_rows(apps(sf), TODAY, "closed")[0]] == [(c, 18, False)]
    assert [r.app.job.id for r in application_rows(apps(sf), TODAY, "all")[0]] == [c, a, e, b, d]


def test_pipeline(session_factory):
    sf = session_factory
    found(sf, {"python": [[p(str(i)) for i in range(1, 7)]]})
    application(sf, "1", ("applied", D(1)), ("hr_interview", D(5)), ("technical_interview", D(9)))  # reply in 4 days
    application(sf, "2", ("applied", D(2)), ("offer", D(12)))      # jumped to Offer; reply in 10 days
    application(sf, "3", ("applied", D(3)), ("rejected", D(4)))    # reply in 1 day
    application(sf, "4", ("applied", D(3)), ("no_response", D(25)))
    application(sf, "5", ("applied", D(6)), ("withdrawn", D(7)))
    application(sf, "6")
    result = pipeline(apps(sf))
    assert result.total == 6
    assert result.reached == [("applied", 6), ("hr_interview", 2), ("technical_interview", 2), ("final_interview", 1),
                              ("offer", 1)]
    assert result.ended == [("rejected", 1), ("withdrawn", 1), ("no_response", 1)]
    assert (result.reply_rate, result.median_reply_days) == (50, 4)
    empty = pipeline([])
    assert (empty.total, empty.reply_rate, empty.median_reply_days) == (0, None, None)
    assert empty.reached[0] == ("applied", 0)


def test_activity_weeks_use_local_time(session_factory):
    sf = session_factory
    found(sf, {"python": [[p("3")]]}, when=datetime(2026, 10, 25, 22, 30))   # Sunday 23:30 in Belgrade
    found(sf, {"python": [[p("1"), p("2")]]}, when=datetime(2026, 10, 25, 23, 30))  # Monday 00:30
    set_job(sf, "3", status="hidden", status_changed_at=datetime(2026, 10, 12, 10, 0))
    application(sf, "1", ("applied", D(26)), ("hr_interview", D(27)))
    application(sf, "2", ("applied", D(21)), ("final_interview", D(22)))
    with sf() as s:
        weeks = activity(s, load_applications(s), TODAY, TZ)
    assert len(weeks) == 12
    assert [w.start for w in weeks[:3]] + [weeks[-1].start] == [D(26), D(19), D(12), D(10, 8)]
    assert [(w.found, w.applied, w.hidden, w.interviews) for w in weeks[:3]] == [(2, 1, 0, 1), (1, 1, 0, 1), (0, 0, 1, 0)]


def test_breakdowns(session_factory):
    sf = session_factory
    found(sf, {"php": [[p("1"), p("2")]], "laravel": [[p("1"), p("3")]]},
          listings=(("php remote", "php"), ("laravel remote", "laravel")))
    set_job(sf, "1", workplace="remote", language="de", seniority="Mid-Senior level")
    set_job(sf, "2", workplace=None, language="en", seniority=None)
    set_job(sf, "3", workplace="hybrid", language="en", seniority=None)
    application(sf, "1", ("applied", D(1)), ("technical_interview", D(5)))
    application(sf, "2", ("applied", D(2)), ("rejected", D(3)))
    application(sf, "3", ("applied", D(3)), ("offer", D(9)))
    b = breakdowns(apps(sf))
    rows = {k: [(r.label, r.applied, r.replied, r.interviews, r.offers) for r in v] for k, v in b.items()}
    assert list(rows) == ["Listing", "Workplace", "Language", "Seniority"]
    assert rows["Listing"] == [("laravel remote", 2, 2, 2, 1), ("php remote", 2, 2, 1, 0)]
    assert rows["Workplace"] == [("Hybrid", 1, 1, 1, 1), ("Remote", 1, 1, 1, 0), ("Unknown", 1, 1, 0, 0)]
    assert rows["Language"] == [("English", 2, 2, 1, 1), ("German", 1, 1, 1, 0)]
    assert rows["Seniority"] == [("Unknown", 2, 2, 1, 1), ("Mid-Senior level", 1, 1, 1, 0)]
    assert pipeline(apps(sf)).total == 3  # job 1 counted once


def test_applied_job_without_steps(session_factory):
    sf = session_factory
    found(sf, {"python": [[p("1")]]})
    application(sf, "1")
    (app,) = apps(sf)
    assert (app.applied_on, app.first_reply_on, app.latest_on) == (None, None, None)
    assert pipeline([app]).reached[0] == ("applied", 1)
    with sf() as s:
        assert sum(w.applied for w in activity(s, [app], TODAY, TZ)) == 0
    (row,), _ = application_rows([app], TODAY, "open")
    assert (row.waiting, row.stale, row.open) == (None, False, True)
