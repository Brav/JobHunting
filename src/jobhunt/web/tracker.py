"""All jobs: every job found, with status tabs, filters, chips, sorting and infinite scroll."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import select

from jobhunt.filters import (JobFilters, company_names, filter_options, filtered_jobs, hidden_count, reveal_all,
                             tab_counts)
from jobhunt.models import Listing
from jobhunt.timeutil import utcnow
from jobhunt.web.common import is_htmx, render, session
from jobhunt.web.job import prepare_job

router = APIRouter()

PER_PAGE = 50
TAB_LABELS = [("active", "Active"), ("undecided", "Undecided"), ("saved", "Saved"), ("applied", "Applied"),
              ("hidden", "Hidden"), ("all", "All")]


def _url(query: str, **extra) -> str:
    parts = [query] if query else []
    parts += [f"{k}={v}" for k, v in extra.items()]
    return "/jobs" + ("?" + "&".join(parts) if parts else "")


@router.get("/jobs", response_class=HTMLResponse)
def all_jobs(request: Request):
    f = JobFilters.from_query(request.query_params)
    raw_cursor = request.query_params.get("cursor", "")
    cursor = int(raw_cursor) if raw_cursor.isdecimal() else None
    now = utcnow()
    with session(request) as s:
        jobs, total = filtered_jobs(s, f, now, limit=PER_PAGE + 1, cursor=cursor)  # one extra: are there more?
        if not is_htmx(request):
            counts = tab_counts(s, f, now)
            hidden_n = hidden_count(s, f, now)
            options = filter_options(s)
            listings = list(s.exec(select(Listing).order_by(Listing.id)).all())
    more = len(jobs) > PER_PAGE
    jobs = jobs[:PER_PAGE]
    next_url = _url(f.query(), cursor=jobs[-1].id) if more else None
    empty = "No jobs match these filters"
    if is_htmx(request):
        return render(request, "_job_items.html", jobs=jobs, next_url=next_url, cursor=cursor, empty_message=empty)
    chips = [(label, _url(query)) for label, query in f.chips()]
    listing = next((l for l in listings if l.id == f.listing_id), None)
    if listing:
        chips.insert(0, (f"Listing: {listing.name}", _url(f.query(listing_id=None))))
    raw_job = request.query_params.get("job", "")
    selected = int(raw_job) if raw_job.isdecimal() else None
    pane = prepare_job(request, selected) if selected and any(j.id == selected for j in jobs) else None
    return render(
        request, "tracker.html", jobs=jobs, total=total, cursor=None, next_url=next_url, filters=f, counts=counts,
        tab_labels=TAB_LABELS, tab_url=lambda tab: _url(f.query(tab=tab)), chips=chips,
        clear_url=_url(JobFilters(tab=f.tab).query()), options=options, listings=listings, pane=pane,
        selected_id=selected, empty_message=empty, hide_hidden=f.tab not in ("hidden", "all"),
        focus_url="/focus?" + "&".join(x for x in ("source=jobs", f.query()) if x),
        filter_action="/jobs", filter_hidden=[("tab", f.tab)] if f.tab != "active" else [],
        hidden_n=hidden_n, show_url=_url(reveal_all(f).query()),
        show_listing=True, show_sort=True,
    )


@router.get("/companies", response_class=HTMLResponse)
def companies(request: Request, company: str = "", q: str = ""):
    with session(request) as s:
        names = company_names(s, company or q)
    return render(request, "_company_options.html", names=names)
