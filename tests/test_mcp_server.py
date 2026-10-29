import asyncio
from zoneinfo import ZoneInfo

import pytest

from jobhunt import mcp_server
from jobhunt.models import Job

TZ = ZoneInfo("Europe/Belgrade")


def seed(sf):
    with sf() as s:
        a = Job(external_id="1", title="Senior PHP Developer", company="Acme", location="Remote", url="https://a",
                eligibility="eligible", best_score=1100, description="Laravel and Vue", salary_max_eur=60000)
        b = Job(external_id="2", title="Python Engineer", company="Globex", location="Berlin", url="https://b",
                status="interested", eligibility="not_eligible", eligibility_override="eligible", best_score=10)
        c = Job(external_id="3", title="Go Developer", company="Initech", location="Madrid", url="https://c",
                eligibility="not_eligible", best_score=5)
        s.add_all([a, b, c])
        s.commit()
        return a.id, b.id, c.id


def test_tools(session_factory):
    a, b, c = seed(session_factory)
    tools = mcp_server.Tools(session_factory, TZ)
    assert [j["id"] for j in tools.search_jobs()] == [a, b, c]  # best first
    assert [j["id"] for j in tools.search_jobs("laravel")] == [a]
    assert [j["id"] for j in tools.search_jobs(eligibility="eligible")] == [a, b]  # the user's override counts
    assert [j["id"] for j in tools.search_jobs(status="interested")] == [b]
    assert tools.search_jobs(limit=1)[0]["salary"] == "€60k"
    with pytest.raises(ValueError, match="Unknown status"):
        tools.search_jobs(status="maybe")
    job = tools.get_job(a)
    assert (job["title"], job["description"], job["steps"]) == ("Senior PHP Developer", "Laravel and Vue", [])
    with pytest.raises(ValueError, match="No job 999"):
        tools.get_job(999)
    assert tools.set_job_status(a, "applied")["status"] == "applied"
    assert tools.add_application_step(a, "hr_interview", "2026-10-27", "Call with Ana")["stage"] == "hr_interview"
    assert [(x["id"], x["stage"]) for x in tools.list_applications()] == [(a, "hr_interview")]
    assert tools.list_applications("closed") == []
    with pytest.raises(ValueError, match="Mark the job Applied first"):
        tools.add_application_step(b, "rejected", "2026-10-27")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.add_application_step(a, "rejected", "yesterday")
    summary = tools.stats_summary()
    assert summary["jobs"]["applied"] == 1 and summary["applications"]["open"] == 1
    assert [g["label"] for g in summary["goals"]] == ["Applied this week", "Reviewed this week"]


def test_server_has_the_six_tools(session_factory):
    server = mcp_server.build_server(session_factory, TZ)
    assert {t.name for t in asyncio.run(server.list_tools())} == {
        "search_jobs", "get_job", "list_applications", "stats_summary", "add_application_step", "set_job_status"}


def test_errors_reach_the_model(session_factory):
    from mcp.server.mcpserver.exceptions import ToolError

    server = mcp_server.build_server(session_factory, TZ)
    with pytest.raises(ToolError, match="No job 999"):
        asyncio.run(server.call_tool("get_job", {"job_id": 999}))
    with pytest.raises(ToolError, match="Unknown status 'maybe'"):
        asyncio.run(server.call_tool("search_jobs", {"status": "maybe"}))


def test_old_database_is_refused(engine):
    from sqlalchemy import text

    assert mcp_server.schema_problem(engine) is None
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE jobs DROP COLUMN auto_closed_on"))
    assert "Start the web app once" in mcp_server.schema_problem(engine)


def test_today_uses_the_profile_zone(session_factory, monkeypatch):
    from dataclasses import replace
    from datetime import date, datetime
    from jobhunt.eligibility import DEFAULT_PROFILE, save_profile
    monkeypatch.setattr(mcp_server, "utcnow", lambda: datetime(2026, 10, 29, 12, 0))  # already the 30th at UTC+14
    with session_factory() as s:
        save_profile(s, replace(DEFAULT_PROFILE, time_zone="Pacific/Kiritimati"), datetime(2026, 10, 29))
    assert mcp_server.Tools(session_factory, ZoneInfo("UTC"))._today() == date(2026, 10, 30)
