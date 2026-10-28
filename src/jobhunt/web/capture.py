"""Add a job from any site: a bookmarklet posts the page here, the user checks the fields, then saves."""

import re
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt import checks
from jobhunt.actions import set_job_status
from jobhunt.capture import fetch_and_parse, job_from_jsonld
from jobhunt.dedup import is_matchable, match_key
from jobhunt.language import detect_language
from jobhunt.models import Job
from jobhunt.timeutil import utcnow
from jobhunt.web.common import check_token, render, session
from jobhunt.workplace import workplace_from

router = APIRouter()

BOOKMARKLET = ("javascript:(()=>{const f=document.createElement('form');f.method='post';f.action='%s/capture';"
               "f.target='_blank';const add=(n,v)=>{const i=document.createElement('input');i.type='hidden';"
               "i.name=n;i.value=v;f.appendChild(i)};add('url',location.href);add('title',document.title);"
               "document.querySelectorAll('script[type=\"application/ld+json\"]').forEach(s=>add('ld',s.textContent));"
               "document.body.appendChild(f);f.submit();f.remove()})()")


@router.get("/capture", response_class=HTMLResponse)
def capture_page(request: Request):
    return render(request, "capture.html", active="jobs", bookmarklet=BOOKMARKLET % str(request.base_url).rstrip("/"))


@router.post("/capture", response_class=HTMLResponse)
def capture_confirm(request: Request, url: str = Form(""), title: str = Form(""), ld: list[str] = Form([])):
    url = url.strip()
    job = job_from_jsonld(ld, url)
    notice = None
    if job is None and url:
        job, notice = fetch_and_parse(url, request.app.state.http_get)
    job = job or {"title": " ".join(title.split()), "url": url, "remote": False}
    return render(request, "capture_confirm.html", active="jobs", job=job, notice=notice,
                  token=request.app.state.form_token)


@router.post("/capture/save")
def capture_save(request: Request, token: str = Form(""), title: str = Form(""), company: str = Form(""),
                 location: str = Form(""), url: str = Form(""), apply_url: str = Form(""),
                 description: str = Form(""), posted_at: str = Form(""), salary_text: str = Form(""),
                 remote: str = Form("")):
    check_token(request, token)
    fields = {"title": " ".join(title.split()), "company": " ".join(company.split()),
              "location": " ".join(location.split()), "url": url.strip(), "apply_url": apply_url.strip(),
              "description": description.strip(), "posted_at": posted_at.strip(),
              "salary_text": " ".join(salary_text.split()), "remote": bool(remote)}
    missing = [name for name in ("title", "company", "url") if not fields[name]]
    web = re.compile(r"https?://", re.IGNORECASE)
    if missing or not web.match(fields["url"]):
        notice = ("Fill in the " + ", ".join(missing) + ".") if missing else "Give the job page as a web address (https://…)."
        return render(request, "capture_confirm.html", status_code=422, active="jobs", job=fields, token=token,
                      notice=notice)
    if not web.match(fields["apply_url"]):
        fields["apply_url"] = ""  # only a web page to apply on, never a script from the page's data
    try:
        posted = date.fromisoformat(fields["posted_at"]) if fields["posted_at"] else None
    except ValueError:
        posted = None
    now = utcnow()
    external_id = "capture:" + fields["url"].split("#")[0]
    key = match_key(fields["title"], fields["company"])
    with session(request) as s:
        job = s.exec(select(Job).where(Job.external_id == external_id)).first()
        if job is None and is_matchable(key):
            job = s.exec(select(Job).where(Job.match_key == key).order_by(Job.id)).first()
        if job is None:
            job = Job(external_id=external_id, source="capture", match_key=key, first_seen_at=now,
                      title=fields["title"], company=fields["company"], location=fields["location"],
                      url=fields["url"], posted_at=posted, apply_url=fields["apply_url"] or None,
                      salary_source_text=fields["salary_text"] or None,
                      workplace=workplace_from(fields["title"], fields["location"], fields["description"],
                                               is_remote=fields["remote"] or None))
            if fields["description"]:
                job.description, job.description_fetched_at = fields["description"], now
                job.language = detect_language(fields["description"])
            s.add(job)
            s.flush()
            checks.refresh(s, job, checks.load_context(s))
            s.commit()
        if job.status == "new":
            set_job_status(s, job.id, "interested", now, request.app.state.settings.tz)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)
