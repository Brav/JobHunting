"""The CV prompt page: the job's description plus the original CV, as a prompt for Claude.ai."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from jobhunt.cv import CVError, build_cv_prompt, current_cv_text
from jobhunt.job_listing import download_listing
from jobhunt.applications import steps_for
from jobhunt.models import Contact, ContactJob, Job, Question
from jobhunt.prompts import KINDS, build_prompt
from jobhunt.sources.base import SourceError
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session

router = APIRouter()


def _job(s: Session, job_id: int) -> Job:
    job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _with_description(request: Request, job_id: int, error: str) -> tuple[Job, str | None]:
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
    return job, description_error


@router.get("/jobs/{job_id}/cv-prompt", response_class=HTMLResponse)
def cv_prompt(request: Request, job_id: int, error: str = ""):
    job, description_error = _with_description(request, job_id, error)
    prompt, cv_error = None, None
    try:
        with session(request) as s:
            cv_text = current_cv_text(s, request.app.state.settings)
    except CVError as e:
        cv_error = str(e)
    else:
        prompt = build_cv_prompt(job, job.description, cv_text)
    return render(request, "cv_prompt.html", job=job, prompt=prompt, cv_error=cv_error,
                  description_error=description_error)


@router.get("/jobs/{job_id}/prompts/{kind}", response_class=HTMLResponse)
def other_prompt(request: Request, job_id: int, kind: str, error: str = ""):
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="No such prompt")
    job, description_error = _with_description(request, job_id, error)
    prompt, cv_error = None, None
    try:
        with session(request) as s:
            cv_text = current_cv_text(s, request.app.state.settings)
    except CVError as e:
        cv_error = str(e)
    else:
        with session(request) as s:
            steps = steps_for(s, job_id)
            people = list(s.exec(select(Contact, ContactJob.relation).join(ContactJob, ContactJob.contact_id == Contact.id)
                                 .where(ContactJob.job_id == job_id)).all())
            questions = list(s.exec(select(Question)).all())
        prompt = build_prompt(kind, job, cv_text, steps, people, questions)
    return render(request, "cv_prompt.html", job=job, prompt=prompt, cv_error=cv_error,
                  description_error=description_error, heading=KINDS[kind], kind=kind)


@router.get("/questions", response_class=HTMLResponse)
def questions_page(request: Request):
    with session(request) as s:
        questions = list(s.exec(select(Question).order_by(Question.company, Question.id)).all())
    return render(request, "questions.html", questions=questions)


@router.post("/questions")
def add_question(request: Request, text: str = Form(""), company: str = Form(""), tags: str = Form("")):
    text = " ".join(text.split())
    if text:
        with session(request) as s:
            s.add(Question(text=text[:500], company=" ".join(company.split()), tags=" ".join(tags.split())))
            s.commit()
    return RedirectResponse("/questions", status_code=303)


@router.post("/questions/{question_id}/delete")
def delete_question(request: Request, question_id: int):
    with session(request) as s:
        question = s.get(Question, question_id)
        if question is not None:
            s.delete(question)
            s.commit()
    return RedirectResponse("/questions", status_code=303)
