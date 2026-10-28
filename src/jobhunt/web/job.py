"""The job page: the whole listing in the app, translated into English when needed."""

import json

from fastapi import APIRouter, Form, HTTPException, Request
from urllib.parse import quote

from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.job_listing import download_listing, translate_description, translate_title
from jobhunt.models import Job
from jobhunt.sources.base import SourceError
from jobhunt.timeutil import utcnow
from jobhunt.translate import TranslationUnavailable
from jobhunt.web.common import render, session

router = APIRouter()


def _job(request: Request, job_id: int) -> Job:
    with session(request) as s:
        job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int, error: str = ""):
    sf, state = request.app.state.session_factory, request.app.state
    job = _job(request, job_id)
    download_error = None
    if not job.description:
        if error:
            download_error = error  # Try again just failed; don't send a second request
        else:
            try:
                download_listing(sf, job_id, state.fetch_page, utcnow())
            except SourceError as e:
                download_error = str(e)
    try:
        translate_title(sf, job_id, state.translator)  # the background pass may not have reached this job yet
    except TranslationUnavailable:
        pass  # translate_description below reports the reason
    reason = translate_description(sf, job_id, state.translator)
    job = _job(request, job_id)
    untranslated = bool(job.description) and job.language not in (None, "en") and not job.description_en
    return render(request, "job.html", job=job, download_error=download_error,
                  details=json.loads(job.details) if job.details else [],
                  translation_error=(reason or job.translation_error) if untranslated else None)


@router.post("/jobs/{job_id}/description")
def retry_download(request: Request, job_id: int, next: str = Form("")):
    _job(request, job_id)
    target = next if next in (f"/jobs/{job_id}", f"/jobs/{job_id}/cv-prompt") else f"/jobs/{job_id}"
    try:
        download_listing(request.app.state.session_factory, job_id, request.app.state.fetch_page, utcnow())
    except SourceError as e:
        target += f"?error={quote(str(e))}"  # the page shows this instead of downloading again
    return RedirectResponse(target, status_code=303)
