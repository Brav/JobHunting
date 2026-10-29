"""Shared web helpers: templates, Jinja filters, and the context every page needs."""

import re
import secrets
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlmodel import Session

from jobhunt.applications import CHANNELS, STAGE_LABELS, STAGES, stage_kind
from jobhunt.blocks import HIDE_REASONS, SCOPES, company_key
from jobhunt.eligibility import VERDICT_LABELS, VERDICT_SYMBOLS, VERDICTS, effective
from jobhunt import salary
from jobhunt.preferences import is_paused, load_feed_errors, load_preferences
from jobhunt.prompts import KINDS as PROMPT_KINDS
from jobhunt.sources.feeds import SOURCE_LABELS
from jobhunt.language import language_name
from jobhunt.reports import run_summary
from jobhunt.timeutil import local_date, to_local, utcnow
from jobhunt.workplace import WORKPLACE_LABELS

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def local_dt(dt: datetime | None, tz: ZoneInfo) -> str:
    return to_local(dt, tz).strftime("%Y-%m-%d %H:%M") if dt else "—"


def when(dt: datetime, tz: ZoneInfo, today: date) -> str:
    """'Today 10:00' for today's runs, '2026-10-27 10:00' otherwise."""
    local = to_local(dt, tz)
    return f"Today {local:%H:%M}" if local.date() == today else f"{local:%Y-%m-%d %H:%M}"


def days_ago(d: date | None, today: date) -> str:
    if d is None:
        return "—"
    days = (today - d).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


# Listings are third-party text: raw HTML is shown as text and unsafe links (javascript:) are not linked.
_markdown = MarkdownIt("commonmark", {"html": False})


def markdown(text: str | None) -> Markup:
    return Markup(_markdown.render(text or ""))


def web_url(url: str | None) -> str:
    """A link from job data only if it is a web address: never a javascript: or data: link from a feed or a page."""
    return url if url and re.match(r"https?://", url.strip(), re.IGNORECASE) else "#"


templates.env.filters.update(local_dt=local_dt, when=when, days_ago=days_ago, markdown=markdown,
                             language_name=language_name, web_url=web_url)


STATUS_LABELS = {"new": "New", "interested": "Saved", "applied": "Applied", "hidden": "Hidden"}


def job_tags(job) -> list[tuple[str, str]]:
    """Up to three (label, kind) tags for a job row: eligibility, Closed for a saved or applied job no longer taking
    applications, workplace, then the status (or stage) once decided, else the language; seniority when there is room."""
    verdict = effective(job)
    tags = [(f"{VERDICT_SYMBOLS[verdict]} {VERDICT_LABELS[verdict]}", f"elig-{verdict}")]
    if job.closed_at and job.status != "new":
        tags.append(("Closed", "stage-closed"))  # a saved job that no longer takes applications
    if job.workplace in WORKPLACE_LABELS:
        tags.append((WORKPLACE_LABELS[job.workplace], ""))
    if job.status == "applied" and job.stage:
        tags.append((STAGE_LABELS[job.stage], stage_kind(job.stage)))
    elif job.status != "new":
        tags.append((STATUS_LABELS[job.status], f"status-{job.status}"))
    elif job.language and job.language != "en":
        tags.append((language_name(job.language), "lang"))
    if job.status == "new" and job.seniority:
        tags.append((job.seniority, ""))
    return tags[:3]


def job_age(job, now: datetime, tz: ZoneInfo) -> tuple[str, str]:
    """Short age of the posting ("today", "3d", "2w") or, without a posting date, of when it was found ("5h")."""
    today = local_date(now, tz)
    if job.posted_at:
        days = (today - job.posted_at).days
        short = "today" if days <= 0 else f"{days}d" if days < 14 else f"{days // 7}w"
        return short, f"Posted {job.posted_at.isoformat()}"
    minutes = int((now - job.first_seen_at).total_seconds() // 60)
    short = f"{max(minutes, 1)}m" if minutes < 60 else f"{minutes // 60}h" if minutes < 1440 else f"{minutes // 1440}d"
    return short, f"Found {local_dt(job.first_seen_at, tz)}"


templates.env.filters.update(tags=job_tags, stage_kind=stage_kind,
                             salary_short=lambda job: salary.short(job.salary_min_eur, job.salary_max_eur))
templates.env.globals.update(status_labels=STATUS_LABELS, workplace_labels=WORKPLACE_LABELS, stages=STAGES,
                             stage_labels=STAGE_LABELS, verdicts=VERDICTS,
                             verdict_labels=VERDICT_LABELS, verdict_symbols=VERDICT_SYMBOLS,
                             hide_reasons=HIDE_REASONS, rule_scopes=SCOPES, channels=CHANNELS,
                             company_key=company_key, prompt_kinds=PROMPT_KINDS, source_labels=SOURCE_LABELS,
                             contact_relations={"referral": "Referral", "recruiter": "Recruiter", "interviewer": "Interviewer", "other": "Other"})


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def session(request: Request) -> Session:
    return request.app.state.session_factory()


def render(request: Request, name: str, status_code: int = 200, **ctx):
    tz = request.app.state.settings.tz
    coordinator = request.app.state.coordinator
    with session(request) as s:
        summary = run_summary(s)
        prefs = load_preferences(s)
        feed_errors = {name: {**e, "since": datetime.fromisoformat(e["since"])}
                       for name, e in load_feed_errors(s).items() if name in prefs.sources}
    now = utcnow()
    paused = prefs.paused_until if is_paused(prefs, local_date(now, tz)) else ""
    base = {"tz": tz, "now": now, "today": local_date(now, tz), "summary": summary,
            "age": lambda job: job_age(job, now, tz),
            "running": coordinator.is_running, "next_run": coordinator.next_run_at(),
            "results_cap": request.app.state.settings.results_per_listing, "paused_until": paused,
            "feed_errors": feed_errors}
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)


def apply_time_zone(app, tz: ZoneInfo) -> None:
    """The user's time zone changed: pages read it from app.state.settings, and the scheduler moves its jobs."""
    app.state.settings = replace(app.state.settings, tz=tz)
    app.state.coordinator.set_time_zone(tz)


def check_token(request: Request, token: str) -> None:
    """Forms that store what another site sent need the token from this app's own page, so no other site can post."""
    if not secrets.compare_digest(token, request.app.state.form_token):
        raise HTTPException(status_code=403, detail="This form has expired — open it again")
