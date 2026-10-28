"""The digest page: what's new since the user last marked it read."""

from dataclasses import replace
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.digest import build_digest
from jobhunt.preferences import load_preferences, save_preferences
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import render, session

router = APIRouter()


def since_of(prefs, now: datetime) -> datetime:
    try:
        return datetime.fromisoformat(prefs.last_digest_at) if prefs.last_digest_at else now - timedelta(hours=24)
    except ValueError:
        return now - timedelta(hours=24)


@router.get("/digest", response_class=HTMLResponse)
def digest_page(request: Request):
    tz, now = request.app.state.settings.tz, utcnow()
    with session(request) as s:
        prefs = load_preferences(s)
        digest = build_digest(s, prefs, since_of(prefs, now), now, local_date(now, tz), tz)
    return render(request, "digest.html", active="digest", digest=digest)


@router.post("/digest/read")
def mark_read(request: Request):
    with session(request) as s:
        save_preferences(s, replace(load_preferences(s), last_digest_at=utcnow().isoformat()), utcnow())
    return RedirectResponse("/digest", status_code=303)
