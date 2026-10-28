"""Application steps: add or delete a dated stage on an applied job."""

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from jobhunt.applications import StepError, add_step, delete_step
from jobhunt.stats import application_row, load_application
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import render, session
from jobhunt.models import Job
from jobhunt.preferences import load_preferences
from jobhunt.web.job import pane_response

router = APIRouter()


def _row_response(request: Request, job_id: int, step_error: str = "", status_code: int = 200):
    """The job's row on the Stats page's applications table."""
    with session(request) as s:
        app = load_application(s, job_id)
        prefs = load_preferences(s)
    today = local_date(utcnow(), request.app.state.settings.tz)
    return render(request, "_stats_app_row.html", status_code=status_code, job_id=job_id, step_error=step_error,
                  row=application_row(app, today, prefs) if app else None)


@router.post("/jobs/{job_id}/followed-up", response_class=HTMLResponse)
def followed_up(request: Request, job_id: int, view: str = Form("pane")):
    """The user followed up today: the reminder (and the auto-close count) start again from now."""
    with session(request) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job.followed_up_on = local_date(utcnow(), request.app.state.settings.tz)
        s.add(job)
        s.commit()
    return _row_response(request, job_id) if view == "stats-row" else pane_response(request, job_id)


@router.post("/jobs/{job_id}/steps", response_class=HTMLResponse)
def add_application_step(request: Request, job_id: int, stage: str = Form(""), on_date: str = Form(""),
                         note: str = Form(""), view: str = Form("pane")):
    now = utcnow()
    try:
        with session(request) as s:
            try:
                day = date.fromisoformat(on_date)
            except ValueError:
                raise StepError("Enter a valid date")
            add_step(s, job_id, stage, day, note, local_date(now, request.app.state.settings.tz), now)
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
    except StepError as e:
        if view == "stats-row":
            return _row_response(request, job_id, str(e), 422)
        return pane_response(request, job_id, step_error=str(e), status_code=422)
    return _row_response(request, job_id) if view == "stats-row" else pane_response(request, job_id)


@router.post("/jobs/{job_id}/steps/{step_id}/delete", response_class=HTMLResponse)
def delete_application_step(request: Request, job_id: int, step_id: int):
    with session(request) as s:
        try:
            delete_step(s, job_id, step_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="Step not found")
    return pane_response(request, job_id)
