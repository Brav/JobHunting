"""The CV prompt page: the job's description plus the original CV, as a prompt for Claude.ai."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from jobhunt.cv import CVError, build_cv_prompt, read_cv_text
from jobhunt.models import Job
from jobhunt.sources.base import SourceError
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session

router = APIRouter()


def _job(s: Session, job_id: int) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _ensure_description(request: Request, job_id: int) -> str | None:
    """Fetch and store the description if it's missing. Returns the failure reason, if any."""
    with session(request) as s:
        job = _job(s, job_id)
        if job.description:
            return None
        external_id = job.external_id
    try:
        description = request.app.state.describe(external_id)  # network call, outside any transaction
    except SourceError as e:
        return str(e)
    with session(request) as s:
        job = _job(s, job_id)
        job.description, job.description_fetched_at = description, utcnow()
        s.add(job)
        s.commit()
    return None


@router.get("/jobs/{job_id}/cv-prompt", response_class=HTMLResponse)
def cv_prompt(request: Request, job_id: int):
    description_error = _ensure_description(request, job_id)
    with session(request) as s:
        job = _job(s, job_id)
    prompt, cv_error = None, None
    try:
        cv_text = read_cv_text(request.app.state.settings.cv_path)
    except CVError as e:
        cv_error = str(e)
    else:
        prompt = build_cv_prompt(job, job.description, cv_text)
    return render(request, "cv_prompt.html", job=job, prompt=prompt, cv_error=cv_error,
                  description_error=description_error)


@router.post("/jobs/{job_id}/description")
def retry_description(request: Request, job_id: int):
    _ensure_description(request, job_id)
    return RedirectResponse(f"/jobs/{job_id}/cv-prompt", status_code=303)
