"""The daily report page and the job status buttons."""

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.actions import set_job_status
from jobhunt.models import Listing
from jobhunt.reports import report_dates, report_for_date
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session

router = APIRouter()


def _report(request: Request, day: date | None):
    tz = request.app.state.settings.tz
    with session(request) as s:
        dates = report_dates(s, tz)
        has_listings = s.exec(select(Listing.id)).first() is not None
        if day is None and dates:
            day = dates[-1]
        sections = report_for_date(s, day, tz) if day else []
    return render(
        request, "report.html", day=day, sections=sections, ran=day in dates, has_listings=has_listings,
        prev_day=max((d for d in dates if day and d < day), default=None),
        next_day=min((d for d in dates if day and d > day), default=None),
    )


@router.get("/", response_class=HTMLResponse)
def latest_report(request: Request):
    return _report(request, None)


@router.get("/report")
def jump_to_report(day: date | None = None):
    return RedirectResponse(f"/report/{day.isoformat()}" if day else "/", status_code=303)


@router.get("/report/{day}", response_class=HTMLResponse)
def report_for_day(request: Request, day: date):
    return _report(request, day)


@router.post("/jobs/{job_id}/status", response_class=HTMLResponse)
def change_status(request: Request, job_id: int, status: str = Form(""), view: str = ""):
    with session(request) as s:
        try:
            job = set_job_status(s, job_id, status, utcnow())
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown status")
        except LookupError:
            raise HTTPException(status_code=404, detail="Job not found")
    if view == "page":
        return render(request, "_job_actions.html", job=job)
    if job.status == "hidden":
        return HTMLResponse("")  # htmx swaps the row out
    return render(request, "_job_row.html", job=job)
