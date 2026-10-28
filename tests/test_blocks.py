from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import select, update

from fakes import FakeClock, FakeSource, add_listing, job_id, make_posting as p
from jobhunt.blocks import blocked_reason, company_key
from jobhunt.filters import JobFilters, filtered_jobs, hidden_count
from jobhunt.models import Job, NegativeRule
from jobhunt.runner import run_daily
from jobhunt.timeutil import utcnow

NOW = datetime(2026, 10, 28, 9, 0)


def job(title="PHP Developer", company="Acme", description=""):
    return Job(external_id="x", title=title, company=company, location="", url="u", description=description)


def test_blocked_reason():
    rules = [NegativeRule(phrase="Java", scope="title", active=True),
             NegativeRule(phrase="Magento", scope="anywhere", active=True),
             NegativeRule(phrase="Cobol", scope="anywhere", active=False)]
    assert blocked_reason(job(company="ACME  GmbH"), {company_key("acme gmbh")}, rules) == "Company blocked"
    assert blocked_reason(job(title="Senior Java Developer"), set(), rules) == 'Keyword "Java" in title'
    assert blocked_reason(job(title="JavaScript Developer"), set(), rules) is None
    assert blocked_reason(job(description="We run Magento 2 shops."), set(), rules) == 'Keyword "Magento"'
    assert blocked_reason(job(description="Legacy Cobol"), set(), rules) is None


def seeded(app):
    sf = app.state.session_factory
    add_listing(sf, "A", keywords="python")
    clock = FakeClock(utcnow())
    run_daily(sf, FakeSource({"python": [[p("1", company="Spammy Recruiting"), p("2", company="Spammy Recruiting"),
                                          p("3", title="Java Developer", company="Good Co")]]}),
              trigger="manual", clock=clock, sleep=clock.sleep)
    return [job_id(sf, i) for i in ("1", "2", "3")]


def statuses(app):
    with app.state.session_factory() as s:
        return {j.external_id: (j.status, j.blocked_by) for j in s.exec(select(Job))}


def test_block_company_and_keyword_hide_new_jobs(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        one, two, three = seeded(app)
        with app.state.session_factory() as s:
            s.exec(update(Job).where(Job.id == two).values(status="interested"))
            s.commit()
        r = c.post(f"/jobs/{one}/block-company")
        assert r.status_code == 200 and "Unblock company" in r.text
        assert statuses(app)["1"] == ("new", "Company blocked") and statuses(app)["2"][1] == "Company blocked"
        r = c.post("/blocks/rules", data={"phrase": "Java", "scope": "title"}, follow_redirects=False)
        assert r.status_code == 303 and statuses(app)["3"][1] == 'Keyword "Java" in title'
        with app.state.session_factory() as s:
            assert [j.external_id for j in filtered_jobs(s, JobFilters(), utcnow())[0]] == ["2"]  # saved stays
            assert hidden_count(s, JobFilters(), utcnow()) == 2
            shown = filtered_jobs(s, JobFilters(blocked="show"), utcnow())[0]
            assert sorted(j.external_id for j in shown) == ["1", "2", "3"]
        assert "2 hidden by your rules · Show" in c.get("/jobs").text
        settings = c.get("/settings").text
        assert "Spammy Recruiting" in settings and "Java" in settings
        rule = c.get("/settings").text.split('action="/blocks/rules/')[1].split("/")[0]
        c.post(f"/blocks/rules/{rule}/remove")
        c.post("/blocks/companies/remove", data={"name": "Spammy Recruiting"})
        assert all(blocked is None for _, blocked in statuses(app).values())


def test_hide_reason(app_factory):
    app = app_factory()
    with TestClient(app) as c:
        one, two, three = seeded(app)
        c.post(f"/jobs/{three}/status", data={"status": "hidden"})
        assert c.post(f"/jobs/{three}/hide-reason", data={"reason": "stack"}).status_code == 204
        assert c.post(f"/jobs/{three}/hide-reason", data={"reason": "nope"}).status_code == 400
        c.post(f"/jobs/{one}/status", data={"status": "hidden"})
        c.post(f"/jobs/{one}/hide-reason", data={"reason": "company"})
        assert statuses(app)["2"][1] == "Company blocked"
        stats = c.get("/stats").text
        assert "Why I hid" in stats and "Stack" in stats


def test_block_changes_only_recheck_the_block(app_factory, monkeypatch):
    """Blocking re-decides only `blocked_by`: the full check of every job held the write lock for seconds."""
    from jobhunt import checks

    app = app_factory()
    with TestClient(app) as c:
        one, _, three = seeded(app)

        def full_check(*args, **kwargs):
            raise AssertionError("a block change ran the full check")
        monkeypatch.setattr(checks, "refresh", full_check)
        c.post(f"/jobs/{one}/block-company")
        c.post("/blocks/rules", data={"phrase": "Java", "scope": "title"})
        assert statuses(app)["1"][1] == "Company blocked" and statuses(app)["3"][1] == 'Keyword "Java" in title'
        c.post("/blocks/companies/remove", data={"name": "Spammy Recruiting"})
        assert statuses(app)["1"][1] is None
