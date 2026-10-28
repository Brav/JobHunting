"""The CV prompt page: the job's description plus the original CV, as a prompt for Claude.ai."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from jobhunt.cv import CVError, build_cv_prompt, read_cv_text
from jobhunt.job_listing import download_listing
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


@router.get("/jobs/{job_id}/cv-prompt", response_class=HTMLResponse)
def cv_prompt(request: Request, job_id: int, error: str = ""):
    with session(request) as s:
        job = _job(s, job_id)
    description_error = error or None  # set when Try again just failed; don't send a second request
    if not job.description and not error:
        try:
            download_listing(request.app.state.session_factory, job_id, request.app.state.fetch_page, utcnow())
        except SourceError as e:
            description_error = str(e)
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
