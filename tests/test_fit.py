from fastapi.testclient import TestClient
from sqlmodel import select, update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt import checks
from jobhunt.filters import JobFilters, filtered_jobs
from jobhunt.fit import default_skills, fit, rank, terms_in
from jobhunt.models import Job
from jobhunt.preferences import Preferences, save_preferences
from jobhunt.runner import run_daily
from jobhunt.timeutil import utcnow


def test_terms():
    assert terms_in("We use Golang, Node.js and React Native") == {"Go", "Node.js", "React Native"}
    assert terms_in("JavaScript and TypeScript") == {"JavaScript", "TypeScript"}
    assert terms_in("Go ahead and apply; we express our values") == set()
    assert terms_in("Laravel 11, PHP 8.3, MySQL, Redis, Docker on AWS") == {"Laravel", "PHP", "MySQL", "Redis", "Docker", "AWS"}
    assert terms_in("Java 17 with Spring Boot") == {"Java", "Spring"}
    assert terms_in("C#/.NET and C++") == {"C#", ".NET", "C++"}


def test_fit_score():
    assert fit("PHP, Laravel, Vue and Kubernetes", ("PHP", "Laravel", "Vue", "MySQL")) == \
        (["Laravel", "PHP", "Vue"], ["Kubernetes"], 75)
    assert fit("A friendly team", ("PHP",)) == ([], [], None)


def test_default_skills_from_cv():
    assert set(default_skills("Senior PHP developer: Laravel, Symfony, MySQL and Docker.")) == \
        {"PHP", "Laravel", "Symfony", "MySQL", "Docker"}


def test_best_first_sort_and_excitement(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p(str(i), title=f"Dev {i}") for i in range(1, 5)]]}),
                  trigger="manual", clock=clock, sleep=clock.sleep)
        ids = {i: job_id(sf, str(i)) for i in range(1, 5)}
        with sf() as s:
            save_preferences(s, Preferences(skills=("PHP", "Laravel")), utcnow())
            s.exec(update(Job).values(description="PHP and Laravel with Kubernetes"))
            s.exec(update(Job).where(Job.id == ids[1]).values(eligibility_override="relocate"))
            s.exec(update(Job).where(Job.id == ids[2]).values(description="Java and Kubernetes"))
            s.commit()
            checks.refresh_all(s)
        c.post(f"/jobs/{ids[3]}/excitement", data={"value": "4"})
        with sf() as s:
            first, _ = filtered_jobs(s, JobFilters(sort="best"), utcnow(), limit=2)
            rest, _ = filtered_jobs(s, JobFilters(sort="best"), utcnow(), cursor=first[-1].id)
            order = [j.title for j in first + rest]
            assert order[0] == "Dev 3" and order[-1] == "Dev 1" and order.index("Dev 4") < order.index("Dev 2")
            assert s.get(Job, ids[3]).excitement == 4
        pane = c.get(f"/jobs/{ids[4]}/pane").text
        assert "Fit: 2 of 3" in pane and "Kubernetes ✗" in pane
        assert c.post(f"/jobs/{ids[3]}/excitement", data={"value": "9"}).status_code == 400


def test_changing_skills_rechecks_only_the_fit(app_factory, monkeypatch):
    app = app_factory()
    with TestClient(app) as c:
        sf = app.state.session_factory
        add_listing(sf, "A", keywords="python")
        clock = FakeClock(utcnow())
        run_daily(sf, FakeSource({"python": [[p("1", title="PHP Laravel Dev")]]}), trigger="manual", clock=clock,
                  sleep=clock.sleep)

        def full_check(*args, **kwargs):
            raise AssertionError("a skills change ran the full check")
        monkeypatch.setattr(checks, "refresh", full_check)
        form = {"skills": "PHP, Laravel", "follow_up_days": "7", "auto_close_days": "30",
                "goal_applications_per_week": "5", "goal_reviews_per_week": "50"}
        assert c.post("/settings/preferences", data=form, follow_redirects=False).status_code == 303
        with sf() as s:
            job = s.exec(select(Job)).one()
        assert job.fit_score == 100 and job.best_score == rank(job)
