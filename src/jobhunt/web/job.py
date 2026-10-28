"""The job page: the whole listing in the app, translated into English when needed."""

import json

from fastapi import APIRouter, Form, HTTPException, Request
from urllib.parse import quote

from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.actions import mark_seen
from jobhunt.applications import next_stage, steps_for
from jobhunt.blocks import blocked_keys, company_key
from jobhunt.eligibility import effective
from jobhunt.followups import follow_up_due
from jobhunt.preferences import load_preferences
from jobhunt.stats import load_application
from jobhunt.signals import LIKELY_GHOST
from jobhunt.job_listing import download_listing, translate_description, translate_title
from jobhunt.models import Contact, ContactJob, Job, Listing, Match
from jobhunt.sources.base import SourceBlocked, SourceError
from jobhunt.timeutil import local_date, utcnow
from jobhunt.translate import TranslationUnavailable
from jobhunt.web.common import render, session

router = APIRouter()

LINKEDIN_LIMITED = "LinkedIn is limiting requests right now; try again in a few minutes"


def _job(request: Request, job_id: int) -> Job:
    with session(request) as s:
        job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def prepare_job(request: Request, job_id: int, error: str = "", fetch: bool = True) -> dict:
    """Everything the detail pane shows. With `fetch`, downloads and translates the listing if needed."""
    sf, state = request.app.state.session_factory, request.app.state
    job = _job(request, job_id)
    download_error = None
    if fetch and not job.description:
        if error:
            download_error = error  # Try again just failed; don't send a second request
        elif state.coordinator.linkedin_blocked():
            download_error = LINKEDIN_LIMITED  # skimming mustn't keep hitting LinkedIn while it's refusing
        else:
            try:
                download_listing(sf, job_id, state.fetch_page, utcnow())
            except SourceBlocked as e:
                state.coordinator.note_blocked()
                download_error = str(e)
            except SourceError as e:
                download_error = str(e)
    reason = None
    if fetch:
        try:
            translate_title(sf, job_id, state.translator)  # the background pass may not have reached this job yet
        except TranslationUnavailable:
            pass  # translate_description below reports the reason
        reason = translate_description(sf, job_id, state.translator)
    job = _job(request, job_id)
    untranslated = bool(job.description) and job.language not in (None, "en") and not job.description_en
    with session(request) as s:
        found_by = list(s.exec(select(Listing.name).join(Match, Match.listing_id == Listing.id)
                               .where(Match.job_id == job_id).distinct().order_by(Listing.name)).all())
        steps = steps_for(s, job_id) if job.status == "applied" else []
        due = job.status == "applied" and follow_up_due(job, load_application(s, job_id), load_preferences(s),
                                                        local_date(utcnow(), request.app.state.settings.tz))
        company_blocked = company_key(job.company) in blocked_keys(s)
        people = list(s.exec(select(Contact, ContactJob.relation).join(ContactJob, ContactJob.contact_id == Contact.id)
                             .where(ContactJob.job_id == job_id)).all())
        known = list(s.exec(select(Contact).order_by(Contact.name)).all())
    return {"job": job, "details": json.loads(job.details) if job.details else [], "download_error": download_error,
            "translation_error": (reason or job.translation_error) if untranslated else None,
            "found_by": found_by, "standalone": False, "steps": steps, "stage_default": next_stage(job.stage),
            "step_error": "", "follow_up_due": due, "fit": _fit(job), "people": people, "known_contacts": known, "signals": _signals(job), "company_blocked": company_blocked,
            "scam": json.loads(job.scam_flags) if job.scam_flags else [], "eligibility": {
                "verdict": effective(job), "override": job.eligibility_override,
                "reasons": json.loads(job.eligibility_reasons) if job.eligibility_reasons else []}}


def _fit(job: Job) -> dict | None:
    matched = json.loads(job.fit_matched) if job.fit_matched else []
    missing = json.loads(job.fit_missing) if job.fit_missing else []
    return {"matched": matched, "missing": missing} if matched or missing else None


def _signals(job: Job) -> dict:
    """Freshness and ghost signals for the pane."""
    days = (utcnow().date() - job.first_seen_at.date()).days if job.first_seen_at else 0
    line = [f"first seen {days} days ago" if days > 1 else None,
            f"reposted {job.reposts}×" if job.reposts else None, job.applicants_text]
    reasons = json.loads(job.ghost_reasons) if job.ghost_reasons else []
    return {"line": " · ".join(x for x in line if x), "ghost": (job.ghost_risk or 0) >= LIKELY_GHOST,
            "reasons": reasons, "closed_at": job.closed_at}


def pane_response(request: Request, job_id: int, *, step_error: str = "", status_code: int = 200):
    """The job's detail pane after a change, with its list row updated out of band."""
    standalone = request.headers.get("HX-Current-URL", "").rstrip("/").endswith(f"/jobs/{job_id}")
    pane = {**prepare_job(request, job_id, fetch=False), "standalone": standalone, "step_error": step_error}
    return render(request, "_status_response.html", status_code=status_code, pane=pane, job=pane["job"], oob=True)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int, error: str = ""):
    pane = {**prepare_job(request, job_id, error), "standalone": True}
    mark_seen(request.app.state.session_factory, job_id, utcnow())
    return render(request, "job.html", pane=pane)


@router.get("/jobs/{job_id}/pane", response_class=HTMLResponse)
def job_pane(request: Request, job_id: int):
    pane = prepare_job(request, job_id)
    mark_seen(request.app.state.session_factory, job_id, utcnow())
    return render(request, "_job_pane.html", pane=pane)


@router.post("/jobs/{job_id}/description")
def retry_download(request: Request, job_id: int, next: str = Form("")):
    _job(request, job_id)
    target = next if next in (f"/jobs/{job_id}", f"/jobs/{job_id}/cv-prompt") else f"/jobs/{job_id}"
    try:
        download_listing(request.app.state.session_factory, job_id, request.app.state.fetch_page, utcnow())
    except SourceError as e:
        if isinstance(e, SourceBlocked):
            request.app.state.coordinator.note_blocked()
        target += f"?error={quote(str(e))}"  # the page shows this instead of downloading again
    return RedirectResponse(target, status_code=303)
