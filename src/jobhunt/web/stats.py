"""The Stats page: triage counts, applications by stage, pipeline, weekly activity and breakdowns."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from jobhunt.stats import activity, application_rows, breakdowns, load_applications, overview, pipeline
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import render, session

router = APIRouter()

APP_TABS = [("open", "Open"), ("closed", "Closed"), ("all", "All")]
ACTIVITY_COLUMNS = [("found", "Found"), ("applied", "Applied"), ("hidden", "Hidden"), ("interviews", "Interviews")]


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, apps: str = "open"):
    which = apps if apps in dict(APP_TABS) else "open"
    tz = request.app.state.settings.tz
    today = local_date(utcnow(), tz)
    with session(request) as s:
        applications = load_applications(s)
        tiles = overview(s, today, tz)
        weeks = activity(s, applications, today, tz)
    rows, counts = application_rows(applications, today, which)
    week_max = {key: max(getattr(w, key) for w in weeks) for key, _ in ACTIVITY_COLUMNS}
    return render(request, "stats.html", active="stats", tiles=tiles, rows=rows, counts=counts, which=which,
                  app_tabs=APP_TABS, pipe=pipeline(applications), weeks=weeks, columns=ACTIVITY_COLUMNS,
                  week_max=week_max, quiet=not any(week_max.values()), breakdowns=breakdowns(applications))
