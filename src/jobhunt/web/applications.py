"""Application steps: add or delete a dated stage on an applied job."""

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from jobhunt.applications import StepError, add_step, delete_step
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import session
from jobhunt.web.job import pane_response

router = APIRouter()


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
        return pane_response(request, job_id, step_error=str(e), status_code=422)
    return pane_response(request, job_id)


@router.post("/jobs/{job_id}/steps/{step_id}/delete", response_class=HTMLResponse)
def delete_application_step(request: Request, job_id: int, step_id: int):
    with session(request) as s:
        try:
            delete_step(s, job_id, step_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="Step not found")
    return pane_response(request, job_id)
