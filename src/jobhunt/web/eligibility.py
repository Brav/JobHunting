"""Correcting a job's eligibility by hand."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from jobhunt.fit import rank
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
        job.best_score = rank(job)
        s.add(job)
        s.commit()
    return pane_response(request, job_id)


@router.post("/jobs/{job_id}/excitement", response_class=HTMLResponse)
def set_excitement(request: Request, job_id: int, value: str = Form("")):
    """How much the user wants the job, 1–5 (0 clears it); it ranks the "Best match" sort."""
    if value not in {"0", "1", "2", "3", "4", "5"}:
        raise HTTPException(status_code=400, detail="Rate 1 to 5")
    with session(request) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job.excitement = int(value) or None
        job.best_score = rank(job)
        s.add(job)
        s.commit()
    return pane_response(request, job_id)
