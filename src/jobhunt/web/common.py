"""Shared web helpers: templates, Jinja filters, and the context every page needs."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlmodel import Session

from jobhunt.language import language_name
from jobhunt.reports import run_summary
from jobhunt.timeutil import local_date, to_local, utcnow

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


templates.env.filters.update(local_dt=local_dt, when=when, days_ago=days_ago, markdown=markdown,
                             language_name=language_name)


def session(request: Request) -> Session:
    return request.app.state.session_factory()


def render(request: Request, name: str, status_code: int = 200, **ctx):
    tz = request.app.state.settings.tz
    coordinator = request.app.state.coordinator
    with session(request) as s:
        summary = run_summary(s)
    base = {"tz": tz, "today": local_date(utcnow(), tz), "summary": summary,
            "running": coordinator.is_running, "next_run": coordinator.next_run_at(),
            "results_cap": request.app.state.settings.results_per_listing}
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)
