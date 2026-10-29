"""The job tracker as an MCP server, so Claude Desktop can search jobs and keep applications up to date.

Run: python -m jobhunt.mcp_server (JOBHUNT_DB_PATH points at the app's database). It reads and writes the same
SQLite file as the web app, through the same functions; it never creates or migrates the database.
"""

import datetime as dt
import functools
import json
import sys
from collections.abc import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, inspect
from sqlmodel import Session, SQLModel, col, or_, select

from jobhunt import actions, applications, salary, stats
from jobhunt.eligibility import VERDICTS, effective
from jobhunt.models import JOB_STATUSES, Job
from jobhunt.preferences import load_preferences
from jobhunt.setup import time_zone
from jobhunt.timeutil import local_date, utcnow

DESCRIPTION_MAX = 10_000
INSTRUCTIONS = ("The user's own job-search tracker. Jobs have a status (new = undecided, interested = saved, "
                "applied, hidden) and, once applied, an application stage. Eligibility says whether the user may "
                "take the job: eligible, relocate, check or not_eligible. Ask before changing anything.")


class Tools:
    """The server's tools as plain methods, so they can be tested without a transport."""

    def __init__(self, session_factory: Callable[[], Session], tz: ZoneInfo, public_url: str = ""):
        self.session_factory, self.tz, self.public_url = session_factory, tz, public_url.rstrip("/")

    def _zone(self) -> ZoneInfo:
        """The user's own time zone (their profile's), read on every call as the web app may change it."""
        with self.session_factory() as s:
            return time_zone(s, self.tz)

    def _today(self) -> dt.date:
        return local_date(utcnow(), self._zone())

    def _summary(self, job: Job) -> dict:
        return {"id": job.id, "title": job.title, "company": job.company, "location": job.location,
                "status": job.status, "stage": job.stage, "eligibility": effective(job),
                "salary": salary.short(job.salary_min_eur, job.salary_max_eur) or None, "fit_percent": job.fit_score,
                "posted": job.posted_at.isoformat() if job.posted_at else None, "url": job.url,
                "in_tracker": f"{self.public_url}/jobs/{job.id}" if self.public_url else None}

    def _job(self, s: Session, job_id: int) -> Job:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"No job {job_id}")
        return job

    def search_jobs(self, query: str = "", status: str = "", eligibility: str = "", limit: int = 20) -> list[dict]:
        """Find jobs, best match first. query: words in the title, company, location or description;
        status: new, interested, applied or hidden; eligibility: eligible, relocate, check or not_eligible."""
        if status and status not in JOB_STATUSES:
            raise ValueError(f"Unknown status {status!r}; use one of {', '.join(JOB_STATUSES)}")
        if eligibility and eligibility not in VERDICTS:
            raise ValueError(f"Unknown eligibility {eligibility!r}; use one of {', '.join(VERDICTS)}")
        stmt = select(Job)
        if query.strip():
            like = f"%{query.strip()}%"
            stmt = stmt.where(or_(*(col(c).ilike(like) for c in (Job.title, Job.company, Job.location,
                                                                  Job.description, Job.description_en))))
        if status:
            stmt = stmt.where(Job.status == status)
        if eligibility:
            stmt = stmt.where(func.coalesce(Job.eligibility_override, Job.eligibility, "check") == eligibility)
        stmt = stmt.order_by(col(Job.best_score).desc().nulls_last(), col(Job.first_seen_at).desc())
        with self.session_factory() as s:
            return [self._summary(job) for job in s.exec(stmt.limit(max(1, min(limit, 50)))).all()]

    def get_job(self, job_id: int) -> dict:
        """Everything about one job: its description, why it is or isn't eligible, warning signs and its steps."""
        with self.session_factory() as s:
            job = self._job(s, job_id)
            steps = applications.steps_for(s, job_id)
        reasons = [r.get("text") for r in json.loads(job.eligibility_reasons or "[]")]
        return {**self._summary(job), "apply_url": job.apply_url, "description": (job.description_en or job.description
                                                                                   or "")[:DESCRIPTION_MAX],
                "eligibility_reasons": reasons, "ghost_signs": json.loads(job.ghost_reasons or "[]"),
                "scam_signs": json.loads(job.scam_flags or "[]"),
                "skills_matched": json.loads(job.fit_matched or "[]"), "skills_missing": json.loads(job.fit_missing or "[]"),
                "steps": [{"stage": st.stage, "date": st.on_date.isoformat(), "note": st.note} for st in steps]}

    def list_applications(self, state: str = "open") -> list[dict]:
        """Applications, longest waiting first. state: open (still in progress), closed or all."""
        if state not in ("open", "closed", "all"):
            raise ValueError("state is open, closed or all")
        with self.session_factory() as s:
            rows, _ = stats.application_rows(stats.load_applications(s), self._today(), state, load_preferences(s))
        return [{"id": r.app.job.id, "title": r.app.job.title, "company": r.app.job.company, "stage": r.app.job.stage,
                 "applied_on": r.app.applied_on.isoformat() if r.app.applied_on else None,
                 "last_step_on": r.app.latest_on.isoformat() if r.app.latest_on else None,
                 "days_waiting": r.waiting, "follow_up_due": r.follow_up} for r in rows]

    def stats_summary(self) -> dict:
        """Counts of jobs by status, applications by stage, the reply rate and this week's goals."""
        today = self._today()
        with self.session_factory() as s:
            counts = dict(s.exec(select(Job.status, func.count()).group_by(Job.status)).all())
            apps = stats.load_applications(s)
            _, app_counts = stats.application_rows(apps, today, "all", load_preferences(s))
            goals = stats.weekly_goals(s, apps, load_preferences(s), today, self._zone())
        flow = stats.pipeline(apps)
        return {"jobs": {status: counts.get(status, 0) for status in JOB_STATUSES}, "applications": app_counts,
                "reached": dict(flow.reached), "ended": dict(flow.ended), "reply_rate_percent": flow.reply_rate,
                "goals": [{"label": g.label, "done": g.done, "target": g.target} for g in goals]}

    def add_application_step(self, job_id: int, stage: str, date: str = "", note: str = "") -> dict:
        """Add a step to an applied job's history. stage: applied, hr_interview, technical_interview,
        final_interview, offer, rejected, withdrawn or no_response; date: YYYY-MM-DD, today if empty."""
        today = self._today()
        try:
            on = dt.date.fromisoformat(date) if date else today
        except ValueError:
            raise ValueError("Give the date as YYYY-MM-DD") from None
        with self.session_factory() as s:
            self._job(s, job_id)
            step = applications.add_step(s, job_id, stage, on, note, today, utcnow())
        return {"job_id": job_id, "stage": step.stage, "date": step.on_date.isoformat(), "note": step.note}

    def set_job_status(self, job_id: int, status: str) -> dict:
        """Set a job's status: new (undecided), interested (saved), applied or hidden."""
        with self.session_factory() as s:
            self._job(s, job_id)
            try:
                job = actions.set_job_status(s, job_id, status, utcnow(), self._zone())
            except ValueError:
                raise ValueError(f"Unknown status {status!r}; use one of {', '.join(JOB_STATUSES)}") from None
            return self._summary(job)



def _reported(fn):
    """The tool with its anticipated failures (bad input, unknown job) passed on to the model as messages."""
    from mcp.server.mcpserver.exceptions import ToolError

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            raise ToolError(str(e)) from e
    return run


def build_server(session_factory: Callable[[], Session], tz: ZoneInfo, public_url: str = ""):
    from mcp.server.mcpserver import MCPServer

    tools = Tools(session_factory, tz, public_url)
    server = MCPServer("Job tracker", instructions=INSTRUCTIONS)
    for tool in (tools.search_jobs, tools.get_job, tools.list_applications, tools.stats_summary,
                 tools.add_application_step, tools.set_job_status):
        server.add_tool(_reported(tool))
    return server


def schema_problem(engine: Engine) -> str | None:
    """Why this database can't be used yet: the web app updates it on start, this server never does."""
    db = inspect(engine)
    for table in SQLModel.metadata.sorted_tables:
        have = {c["name"] for c in db.get_columns(table.name)} if db.has_table(table.name) else set()
        missing = [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
        if missing:
            return (f"The database is older than this code (no {missing[0]}). "
                    "Start the web app once so it updates the database, then try again.")
    return None


def main() -> None:
    from jobhunt.config import load_settings
    from jobhunt.db import make_engine, make_session_factory

    settings = load_settings()
    if not settings.db_path.exists():
        sys.exit(f"No job tracker database at {settings.db_path}; set JOBHUNT_DB_PATH to the app's jobhunt.db")
    engine = make_engine(settings.db_path)
    if problem := schema_problem(engine):
        sys.exit(problem)
    build_server(make_session_factory(engine), settings.tz, settings.public_url).run("stdio")


if __name__ == "__main__":
    main()
