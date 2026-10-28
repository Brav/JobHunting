"""Focus mode: the undecided jobs of a list, one at a time, with a summary at the end."""

from dataclasses import replace
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.actions import mark_seen, set_job_status
from jobhunt.filters import JobFilters, ordered_ids, report_jobs
from jobhunt.models import Job
from jobhunt.reports import report_dates
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session
from jobhunt.web.job import prepare_job

router = APIRouter()

ACTIONS = {"save": "interested", "applied": "applied", "hide": "hidden", "later": None}
COUNTERS = ("n_saved", "n_applied", "n_hidden", "n_later")
_COUNTER_FOR = {"save": "n_saved", "applied": "n_applied", "hide": "n_hidden", "later": "n_later"}


def _number(value: str | None) -> int:
    return int(value) if value and value.isdecimal() else 0


@router.get("/focus", response_class=HTMLResponse)
def focus(request: Request):
    params, tz, now = request.query_params, request.app.state.settings.tz, utcnow()
    f = JobFilters.from_query(params)
    from_jobs = params.get("source") == "jobs"
    with session(request) as s:
        if from_jobs:
            # Ordered like All jobs, but with every status so a just-decided job keeps its place.
            ordered = ordered_ids(s, replace(f, tab="all"), now)
            candidates = f.tab in ("active", "undecided", "all")
            back_url = "/jobs" + (f"?{f.query()}" if f.query() else "")
            back_query = "&".join(x for x in ("source=jobs", f.query()) if x)
            name = "All jobs"
        else:
            try:
                day = date.fromisoformat(params.get("day", ""))
            except ValueError:
                dates = report_dates(s, tz)
                day = dates[-1] if dates else None
            jobs = report_jobs(s, day, tz, f, now, include_hidden=True) if day else []
            ordered = [(job.id, job.status) for job in jobs]
            candidates = True
            back_url = f"/report/{day.isoformat()}" + (f"?{f.query()}" if f.query() else "") if day else "/"
            back_query = "&".join(x for x in (urlencode({"day": day.isoformat()}) if day else "", f.query()) if x)
            name = "All listings"
    ids = [job_id for job_id, _ in ordered]
    after = _number(params.get("after"))
    start = ids.index(after) + 1 if after in ids else 0
    current = next((job_id for job_id, status in ordered[start:] if status == "new"), None) if candidates else None
    counters = {name_: _number(params.get(name_)) for name_ in COUNTERS}
    pane = None
    if current:
        pane = {**prepare_job(request, current), "focus": True}
        mark_seen(request.app.state.session_factory, current, now)
    return render(request, "focus.html", pane=pane, position=ids.index(current) + 1 if current else 0,
                  total=len(ids), list_name=name, back_url=back_url, back_query=back_query, counters=counters)


@router.post("/focus/act")
def focus_act(request: Request, job_id: int = Form(...), action: str = Form(...), back: str = Form(""),
              n_saved: str = Form("0"), n_applied: str = Form("0"), n_hidden: str = Form("0"), n_later: str = Form("0")):
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action")
    with session(request) as s:
        if s.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if ACTIONS[action]:
            set_job_status(s, job_id, ACTIONS[action], utcnow(), request.app.state.settings.tz)
    mark_seen(request.app.state.session_factory, job_id, utcnow())
    counters = {"n_saved": _number(n_saved), "n_applied": _number(n_applied), "n_hidden": _number(n_hidden),
                "n_later": _number(n_later)}
    counters[_COUNTER_FOR[action]] += 1
    query = "&".join(x for x in (back, urlencode({"after": job_id, **counters})) if x)
    return RedirectResponse(f"/focus?{query}", status_code=303)
