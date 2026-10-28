"""Contacts: people at companies, their part in each job (referral, recruiter, interviewer), and follow-ups."""

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.blocks import company_key
from jobhunt.models import Contact, ContactJob, Job
from jobhunt.timeutil import local_date, utcnow
from jobhunt.web.common import render, session
from jobhunt.web.job import pane_response

router = APIRouter()

RELATIONS = {"referral": "Referral", "recruiter": "Recruiter", "interviewer": "Interviewer", "other": "Other"}
FIELDS = ("name", "company", "role", "email", "url", "notes")


def _today(request: Request) -> date:
    return local_date(utcnow(), request.app.state.settings.tz)


def _clean(form: dict) -> tuple[dict, list[str]]:
    values = {k: " ".join(str(form.get(k, "")).split()) if k != "notes" else str(form.get(k, "")).strip()
              for k in FIELDS}
    errors = [] if values["name"] else ["Enter a name"]
    follow = str(form.get("follow_up_on", "")).strip()
    try:
        values["follow_up_on"] = date.fromisoformat(follow) if follow else None
    except ValueError:
        errors.append("Enter the follow-up as a date")
        values["follow_up_on"] = None
    return values, errors


def contacts_for_company(session, name: str) -> list[Contact]:
    key = company_key(name)
    return [c for c in session.exec(select(Contact).order_by(Contact.name)).all() if company_key(c.company) == key]


@router.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request, errors: list[str] | None = None, status_code: int = 200):
    today = _today(request)
    with session(request) as s:
        people = list(s.exec(select(Contact)).all())
        counts = {c.id: n for c, n in ((c, len(s.exec(select(ContactJob).where(ContactJob.contact_id == c.id)).all()))
                                       for c in people)}
    # follow-ups due first (oldest first), then everyone else by name
    people.sort(key=lambda c: (not (c.follow_up_on and c.follow_up_on <= today), c.follow_up_on or date.max,
                               c.name.casefold()))
    return render(request, "contacts.html", status_code=status_code, active="contacts", people=people, counts=counts,
                  today=today, errors=errors or [])


@router.post("/contacts")
async def add_contact(request: Request):
    values, errors = _clean(dict(await request.form()))
    if errors:
        return contacts_page(request, errors, 422)
    with session(request) as s:
        s.add(Contact(**values))
        s.commit()
    return RedirectResponse("/contacts", status_code=303)


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_page(request: Request, contact_id: int):
    with session(request) as s:
        contact = s.get(Contact, contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="No such contact")
        jobs = s.exec(select(Job, ContactJob.relation).join(ContactJob, ContactJob.job_id == Job.id)
                      .where(ContactJob.contact_id == contact_id)).all()
    return render(request, "contact.html", active="contacts", contact=contact, jobs=jobs, relations=RELATIONS)


@router.post("/contacts/{contact_id}")
async def edit_contact(request: Request, contact_id: int):
    values, errors = _clean(dict(await request.form()))
    with session(request) as s:
        contact = s.get(Contact, contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="No such contact")
        if errors:
            raise HTTPException(status_code=422, detail="; ".join(errors))
        for key, value in values.items():
            setattr(contact, key, value)
        s.add(contact)
        s.commit()
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/delete")
def delete_contact(request: Request, contact_id: int):
    with session(request) as s:
        contact = s.get(Contact, contact_id)
        if contact is not None:
            s.delete(contact)
            s.commit()
    return RedirectResponse("/contacts", status_code=303)


@router.post("/jobs/{job_id}/contacts", response_class=HTMLResponse)
def link_contact(request: Request, job_id: int, contact_id: str = Form(""), name: str = Form(""),
                 role: str = Form(""), email: str = Form(""), relation: str = Form("other")):
    """Link an existing contact, or add one on the spot, to a job; a referral marks how the user applied."""
    if relation not in RELATIONS:
        raise HTTPException(status_code=400, detail="Unknown relation")
    with session(request) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if contact_id.isdecimal() and (contact := s.get(Contact, int(contact_id))) is not None:
            pass
        elif " ".join(name.split()):
            contact = Contact(name=" ".join(name.split()), company=job.company, role=" ".join(role.split()),
                              email=email.strip())
            s.add(contact)
            s.flush()
        else:
            raise HTTPException(status_code=400, detail="Pick a contact or enter a name")
        link = s.get(ContactJob, (contact.id, job_id)) or ContactJob(contact_id=contact.id, job_id=job_id)
        link.relation = relation
        s.add(link)
        if relation == "referral" and not job.applied_via:
            job.applied_via = "referral"
            s.add(job)
        s.commit()
    return pane_response(request, job_id)


@router.post("/jobs/{job_id}/contacts/{contact_id}/unlink", response_class=HTMLResponse)
def unlink_contact(request: Request, job_id: int, contact_id: int):
    with session(request) as s:
        link = s.get(ContactJob, (contact_id, job_id))
        if link is not None:
            s.delete(link)
            s.commit()
    return pane_response(request, job_id)
