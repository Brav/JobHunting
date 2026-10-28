"""Companies: every company the searches found, and one company's jobs, applications and outcomes."""

from collections import Counter, defaultdict
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import func, select

from jobhunt.blocks import blocked_keys, company_key
from jobhunt.models import Contact, Job, Sighting
from jobhunt.stats import load_applications
from jobhunt.web.common import render, session

router = APIRouter()


def _outcome(apps) -> str:
    replied = sum(1 for a in apps if a.first_reply_on)
    return f"Applied {len(apps)}× · {replied} repl{'y' if replied == 1 else 'ies'}" if apps else ""


@router.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, q: str = ""):
    with session(request) as s:
        jobs = s.exec(select(Job.company, Job.status)).all()
        blocked = blocked_keys(s)
        apps = load_applications(s)
    names, totals, applied = defaultdict(Counter), Counter(), Counter()
    for company, status in jobs:
        key = company_key(company)
        names[key][company] += 1
        totals[key] += 1
    for app in apps:
        applied[company_key(app.job.company)] += 1
    needle = q.strip().casefold()
    rows = sorted(((key, names[key].most_common(1)[0][0], totals[key], applied[key], key in blocked)
                   for key in totals if key and (not needle or needle in key)), key=lambda r: (-r[2], r[1].casefold()))
    return render(request, "companies.html", active="companies", rows=rows, q=q)


@router.get("/companies/{key}", response_class=HTMLResponse)
def company_page(request: Request, key: str):
    with session(request) as s:
        jobs = [j for j in s.exec(select(Job).order_by(Job.first_seen_at.desc())).all() if company_key(j.company) == key]
        if not jobs:
            raise HTTPException(status_code=404, detail="No jobs from this company")
        apps = [a for a in load_applications(s) if company_key(a.job.company) == key]
        sightings = s.exec(select(func.count()).select_from(Sighting).where(Sighting.job_id.in_([j.id for j in jobs]))).one()
        is_blocked = key in blocked_keys(s)
        people = [c for c in s.exec(select(Contact).order_by(Contact.name)).all() if company_key(c.company) == key]
    name = Counter(j.company for j in jobs).most_common(1)[0][0]
    links = [("LinkedIn", f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(name)}"),
             ("Glassdoor", f"https://www.glassdoor.com/Search/results.htm?keyword={quote_plus(name)}"),
             ("Web search", f"https://duckduckgo.com/?q={quote_plus(name + ' careers')}")]
    return render(request, "company.html", active="companies", key=key, company_name=name, jobs=jobs, outcome=_outcome(apps),
                  reposts=sum(j.reposts or 0 for j in jobs), sightings=sightings, is_blocked=is_blocked, links=links,
                  people=people)
