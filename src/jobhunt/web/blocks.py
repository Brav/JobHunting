"""Blocking companies and keywords, and recording why a job was hidden."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from jobhunt import blocks, checks
from jobhunt.models import Job
from jobhunt.timeutil import utcnow
from jobhunt.web.common import session
from jobhunt.web.job import pane_response

router = APIRouter()


def _recheck(request: Request) -> None:
    with session(request) as s:
        checks.refresh_all(s)


def _job(request: Request, job_id: int) -> Job:
    with session(request) as s:
        job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _back(next: str) -> RedirectResponse:
    return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/settings#blocks",
                            status_code=303)


@router.post("/jobs/{job_id}/block-company")
def block_job_company(request: Request, job_id: int):
    job = _job(request, job_id)
    with session(request) as s:
        blocks.block_company(s, job.company, utcnow())
    _recheck(request)
    return pane_response(request, job_id)


@router.post("/jobs/{job_id}/unblock-company")
def unblock_job_company(request: Request, job_id: int):
    job = _job(request, job_id)
    with session(request) as s:
        blocks.unblock_company(s, job.company)
    _recheck(request)
    return pane_response(request, job_id)


@router.post("/blocks/companies")
def add_company(request: Request, name: str = Form(""), next: str = Form("")):
    with session(request) as s:
        blocks.block_company(s, name, utcnow())
    _recheck(request)
    return _back(next)


@router.post("/blocks/companies/remove")
def remove_company(request: Request, name: str = Form(""), next: str = Form("")):
    with session(request) as s:
        blocks.unblock_company(s, name)
    _recheck(request)
    return _back(next)


@router.post("/blocks/rules")
def add_rule(request: Request, phrase: str = Form(""), scope: str = Form("title")):
    with session(request) as s:
        blocks.add_rule(s, phrase, scope, utcnow())
    _recheck(request)
    return _back("")


@router.post("/blocks/rules/{rule_id}/remove")
def remove_rule(request: Request, rule_id: int):
    with session(request) as s:
        blocks.remove_rule(s, rule_id)
    _recheck(request)
    return _back("")


@router.post("/jobs/{job_id}/hide-reason")
def hide_reason(request: Request, job_id: int, reason: str = Form("")):
    """Why the user hid a job (asked in the toast after hiding); "company" also blocks the company."""
    if reason not in blocks.HIDE_REASONS:
        raise HTTPException(status_code=400, detail="Unknown reason")
    with session(request) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job.hide_reason = reason
        s.add(job)
        s.commit()
        if reason == "company":
            blocks.block_company(s, job.company, utcnow())
    if reason == "company":
        _recheck(request)
    return Response(status_code=204)
