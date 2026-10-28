"""The job tracker: every job found, filterable by status, listing and text."""

import math
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import select

from jobhunt.models import Listing
from jobhunt.reports import tracker_jobs
from jobhunt.web.common import render, session

router = APIRouter()

PER_PAGE = 50
STATUS_OPTIONS = [("default", "All except hidden"), ("all", "All"), ("new", "New"),
                  ("interested", "Interested"), ("applied", "Applied"), ("hidden", "Hidden")]


@router.get("/jobs", response_class=HTMLResponse)
def tracker(request: Request,
            status: Literal["default", "all", "new", "interested", "applied", "hidden"] = "default",
            listing: str = "", q: str = "", page: int = Query(1, ge=1)):
    # The filter form sends listing="" for "All listings", so it is parsed here rather than typed as int.
    listing_id = int(listing) if listing.isdecimal() else None
    with session(request) as s:
        jobs, total = tracker_jobs(s, status=status, listing_id=listing_id, q=q, page=page, per_page=PER_PAGE)
        listings = s.exec(select(Listing).order_by(Listing.id)).all()
    pages = max(1, math.ceil(total / PER_PAGE))

    def page_url(n: int) -> str:
        params = {"status": status if status != "default" else None, "listing": listing_id,
                  "q": q or None, "page": n}
        return "/jobs?" + urlencode({k: v for k, v in params.items() if v is not None})

    return render(request, "tracker.html", jobs=jobs, total=total, page=page, pages=pages,
                  status=status, status_options=STATUS_OPTIONS, listing_id=listing_id, listings=listings,
                  q=q, prev_url=page_url(page - 1) if page > 1 else None,
                  next_url=page_url(page + 1) if page < pages else None)
