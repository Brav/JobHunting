"""Correcting a job's eligibility by hand."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from jobhunt.models import Job
from jobhunt.web.common import session
from jobhunt.web.job import pane_response

router = APIRouter()

OVERRIDES = ("eligible", "relocate", "not_eligible", "auto")


@router.post("/jobs/{job_id}/eligibility", response_class=HTMLResponse)
def override_eligibility(request: Request, job_id: int, value: str = Form("")):
    """Set the verdict by hand ("auto" goes back to the rules); answers with the pane and the row."""
    if value not in OVERRIDES:
        raise HTTPException(status_code=400, detail="Unknown verdict")
    with session(request) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job.eligibility_override = None if value == "auto" else value
        s.add(job)
        s.commit()
    return pane_response(request, job_id)
