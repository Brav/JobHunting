from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import select, update

from jobhunt import checks
from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.filters import JobFilters, filtered_jobs
from jobhunt.models import Job, Sighting
from jobhunt.runner import run_daily
from jobhunt.signals import check_closed, ghost
from jobhunt.sources.base import JobPage
from jobhunt.timeutil import utcnow

NOW = datetime(2026, 10, 28, 9, 0)
TODAY = NOW.date()


def run(sf, postings, when, keywords="python"):
    clock = FakeClock(when)
    run_daily(sf, FakeSource({keywords: [postings]}), trigger="manual", clock=clock, sleep=clock.sleep)


def test_one_sighting_per_job_per_run(session_factory):
    sf = session_factory
    add_listing(sf, "A", keywords="python")
    add_listing(sf, "B", keywords="django")
    clock = FakeClock(NOW)
    run_daily(sf, FakeSource({"python": [[p("1")]], "django": [[p("1")]]}), trigger="manual", clock=clock, sleep=clock.sleep)
    run(sf, [p("1", posted_at=date(2026, 10, 30))], NOW + timedelta(days=2))
    with sf() as s:
        rows = s.exec(select(Sighting).order_by(Sighting.seen_at)).all()
    assert len(rows) == 2 and rows[1].posted_at == date(2026, 10, 30)


def test_the_same_job_on_other_boards_is_not_a_repost(session_factory):
    from dataclasses import replace

    sf = session_factory
    add_listing(sf, "A", keywords="python")
    linkedin = p("1", title="Backend Developer", posted_at=date(2026, 10, 27))
    remoteok = replace(p("remoteok:9", title="Backend Developer", posted_at=date(2026, 10, 28)), source="remoteok")
    himalayas = replace(p("himalayas:5", title="Backend Developer", posted_at=date(2026, 10, 29)), source="himalayas")
    run(sf, [linkedin], NOW)
    run(sf, [remoteok], NOW + timedelta(days=1))
    run(sf, [himalayas], NOW + timedelta(days=2))
    with sf() as s:
        (found,) = s.exec(select(Job)).all()
    assert (found.reposts, found.ghost_risk) == (0, 0)


def sight(days_ago, posted, external_id="1"):
    return Sighting(job_id=1, seen_at=NOW - timedelta(days=days_ago), external_id=external_id, posted_at=posted)


def job(**kw):
    values = dict(external_id="1", title="t", company="c", location="", url="u", first_seen_at=NOW)
    return Job(**{**values, **kw})


def test_ghost_score():
    assert ghost(job(), [sight(0, TODAY)], TODAY) == (0, [])
    old = job(first_seen_at=NOW - timedelta(days=40))
    assert ghost(old, [sight(40, date(2026, 9, 18))], TODAY) == (1, ["First seen 40 days ago"])
    reposted = [sight(40, date(2026, 9, 18)), sight(20, date(2026, 10, 8), "2"), sight(1, date(2026, 10, 27), "3")]
    score, reasons = ghost(old, reposted, TODAY)
    assert score == 3 and "Reposted 2×" in reasons
    crowded = job(applicants=250, posted_at=date(2026, 10, 27), description_fetched_at=NOW)
    assert ghost(crowded, [sight(0, date(2026, 10, 27))], TODAY) == (1, ["Over 200 applicants within 2 days"])


def test_ghost_filter_and_display(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        run(sf, [p("1", title="Ghostly Dev"), p("2", title="Fresh Dev")], utcnow())
        ghostly = job_id(sf, "1")
        with sf() as s:  # first seen 47 days ago, reposted three times since
            s.exec(update(Job).where(Job.id == ghostly).values(first_seen_at=utcnow() - timedelta(days=47)))
            for i, days in enumerate((40, 20, 5), start=2):
                s.add(Sighting(job_id=ghostly, seen_at=utcnow() - timedelta(days=days), external_id=f"1-{i}",
                               posted_at=(utcnow() - timedelta(days=days)).date()))
            s.commit()
            checks.refresh_all(s)
            assert [j.title for j in filtered_jobs(s, JobFilters(ghost="hide"), utcnow())[0]] == ["Fresh Dev"]
        assert "↻3" in c.get("/jobs").text
        pane = c.get(f"/jobs/{job_id(sf, '1')}/pane").text
        assert "Likely ghost job" in pane and "Reposted 3×" in pane


def test_closed_check(session_factory):
    sf = session_factory
    add_listing(sf, "A", keywords="python")
    run(sf, [p("1"), p("2")], NOW)
    with sf() as s:
        s.exec(update(Job).where(Job.external_id == "1").values(status="interested"))
        s.commit()
    asked = []

    def fetch(external_id):
        asked.append(external_id)
        return JobPage("Build PHP APIs.", [], closed=True)

    assert check_closed(sf, fetch, NOW, sleep=lambda s: None) == 1
    assert asked == ["1"]
    with sf() as s:
        assert s.exec(select(Job.closed_at).where(Job.external_id == "1")).one() == NOW
