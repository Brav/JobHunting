"""Paste an employer's email; the app proposes the application step it means, and adds it only on Confirm."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.actions import set_job_status
from jobhunt.applications import STAGES, StepError, add_step, next_stage, steps_for
from jobhunt.email_update import candidates, classify, note_for, proposed_stage
from jobhunt.models import Job
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import check_token, render, session

router = APIRouter()
KIND_LABELS = {"received": "Application received", "rejected": "Rejection", "interview": "Interview invitation",
               "offer": "Offer"}


@router.get("/email", response_class=HTMLResponse)
def email_page(request: Request):
    return render(request, "email.html", active="stats", text="", kind=None)


@router.post("/email", response_class=HTMLResponse)
def email_read(request: Request, text: str = Form("")):
    kind, phrase = classify(text)
    with session(request) as s:
        rows = [(job, proposed_stage(kind, job.stage) if kind else next_stage(job.stage))
                for job in candidates(s, text)]
    return render(request, "email.html", active="stats", text=text, read=True, kind=kind, phrase=phrase,
                  kind_label=KIND_LABELS.get(kind), rows=rows, note=note_for(text), token=request.app.state.form_token)


@router.post("/email/confirm")
def email_confirm(request: Request, token: str = Form(""), job_id: int = Form(...), stage: str = Form(""),
                  note: str = Form("")):
    check_token(request, token)
    if stage not in STAGES:
        raise HTTPException(status_code=400, detail="Unknown stage")
    now = utcnow()
    tz = request.app.state.settings.tz
    today = local_date(now, tz)
    with session(request) as s:
        if s.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if s.get(Job, job_id).status != "applied":
            set_job_status(s, job_id, "applied", now, tz)  # starts the history with an Applied step dated today
        if not (stage == "applied" and any(step.stage == "applied" for step in steps_for(s, job_id))):
            try:
                add_step(s, job_id, stage, today, note, today, now)
            except StepError as e:
                raise HTTPException(status_code=422, detail=str(e))
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)
