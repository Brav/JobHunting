"""The daily report as a split view, and status changes from the detail pane."""

import json
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.actions import set_job_status
from jobhunt.filters import JobFilters, filter_options, report_jobs, report_tabs, unseen_count
from jobhunt.models import Job, Listing
from jobhunt.reports import report_dates
from jobhunt.timeutil import utcnow
from jobhunt.web.common import STATUS_LABELS, is_htmx, render, session
from jobhunt.web.job import prepare_job

router = APIRouter()


def _selected(request: Request) -> int | None:
    value = request.query_params.get("job", "")
    return int(value) if value.isdecimal() else None


def _report(request: Request, day: date | None):
    tz, now = request.app.state.settings.tz, utcnow()
    f = JobFilters.from_query(request.query_params)
    unseen = request.query_params.get("unseen") == "1"
    with session(request) as s:
        dates = report_dates(s, tz)
        has_listings = s.exec(select(Listing.id)).first() is not None
        if day is None and dates:
            day = dates[-1]
        tabs = report_tabs(s, day, tz) if day else []
        jobs = report_jobs(s, day, tz, f, now, unseen_only=unseen) if day else []
        unseen_n = unseen_count(s, day, tz) if day else 0
        options = filter_options(s)
    empty = "No unseen jobs left" if unseen else "No new jobs on this day"
    if is_htmx(request):
        return render(request, "_job_items.html", jobs=jobs, offset=0, empty_message=empty)
    selected = _selected(request)
    pane = prepare_job(request, selected) if selected and any(j.id == selected for j in jobs) else None
    day_query = urlencode({"day": day.isoformat()}) if day else ""
    return render(
        request, "report.html", day=day, ran=day in dates, has_listings=has_listings, tabs=tabs, jobs=jobs,
        filters=f, unseen=unseen, unseen_n=unseen_n, options=options, pane=pane, selected_id=selected,
        empty_message=empty, offset=0, hide_hidden=True,
        focus_url=f"/focus?{day_query}&{f.query()}".rstrip("&") if day else "",
        prev_day=max((d for d in dates if day and d < day), default=None),
        next_day=min((d for d in dates if day and d > day), default=None),
    )


@router.get("/", response_class=HTMLResponse)
def latest_report(request: Request):
    return _report(request, None)


@router.get("/report")
def jump_to_report(day: str = ""):
    # The date picker sends day="" when cleared, so it is parsed here rather than typed as a date.
    try:
        target = f"/report/{date.fromisoformat(day).isoformat()}"
    except ValueError:
        target = "/"
    return RedirectResponse(target, status_code=303)


@router.get("/report/{day}", response_class=HTMLResponse)
def report_for_day(request: Request, day: date):
    return _report(request, day)


@router.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def change_status(request: Request, job_id: int, status: str = Form(""), view: str = Form("")):
    view = view or request.query_params.get("view", "")  # the pane sends it in the body, older buttons in the URL
    with session(request) as s:
        current = s.get(Job, job_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Job not found")
        previous = current.status
        try:
            job = set_job_status(s, job_id, status, utcnow())
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown status")
    if view == "pane":
        response = render(request, "_status_response.html", pane=prepare_job(request, job_id, fetch=False),
                          job=job, oob=True)
        response.headers["HX-Trigger-After-Settle"] = json.dumps({"jobStatusChanged": {
            "jobId": job.id, "status": job.status, "previous": previous, "label": STATUS_LABELS[job.status]}})
        return response
    if view == "page":
        return render(request, "_job_actions.html", job=job)
    if job.status == "hidden":
        return HTMLResponse("")  # htmx swaps the row out
    return render(request, "_job_row.html", job=job)
