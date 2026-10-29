"""Listings pages: list, add, edit, switch on/off, delete."""

from collections.abc import Mapping

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from jobhunt.actions import delete_listing, delete_listing_results
from jobhunt.models import Listing
from jobhunt.reports import listing_rows
from jobhunt.schedule import form_values, from_form
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session

router = APIRouter()

REQUIRED = "Required."
DUPLICATE = "A listing with this name already exists."
BAD_RADIUS = "Radius must be a whole number from 0 to 200."
EMPTY_FORM = {"name": "", "keywords": "", "location": "", "radius_km": 25, **form_values(None)}


def validate_listing_form(session: Session, form: Mapping[str, str],
                          listing_id: int | None = None) -> tuple[dict, dict[str, str]]:
    values = {key: (form.get(key) or "").strip() for key in ("name", "keywords", "location", "radius_km")}
    errors = {key: REQUIRED for key in ("name", "keywords", "location") if not values[key]}
    if not values["radius_km"].isdecimal() or int(values["radius_km"]) > 200:
        errors["radius_km"] = BAD_RADIUS
    else:
        values["radius_km"] = int(values["radius_km"])
    if "name" not in errors:
        clash = session.exec(select(Listing).where(Listing.name == values["name"])).first()
        if clash is not None and clash.id != listing_id:
            errors["name"] = DUPLICATE
    return values, errors


def _row(s: Session, listing_id: int):
    return next(row for row in listing_rows(s) if row.listing.id == listing_id)


def _get_listing(s: Session, listing_id: int) -> Listing:
    listing = s.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing


@router.get("/listings", response_class=HTMLResponse)
def listings_page(request: Request):
    with session(request) as s:
        rows = listing_rows(s)
    return render(request, "listings.html", rows=rows, values=EMPTY_FORM, errors={})


def _schedule(choice: str, times: list[str], start: str, every: str, values: dict, errors: dict) -> str | None:
    """The schedule the form describes (None = the default); its choices go back into the form, its errors too."""
    text, problems = from_form(choice, times, start, every)
    values.update(choice=choice, times=times or [""], start=start, every=every)
    if problems:
        errors["schedule"] = " ".join(problems)
    return text


@router.post("/listings", response_class=HTMLResponse)
def create_listing(request: Request, name: str = Form(""), keywords: str = Form(""),
                   location: str = Form(""), radius_km: str = Form(""), schedule: str = Form("default"),
                   times: list[str] = Form([]), start: str = Form(""), every: str = Form("")):
    form = {"name": name, "keywords": keywords, "location": location, "radius_km": radius_km}
    with session(request) as s:
        values, errors = validate_listing_form(s, form)
        text = _schedule(schedule, times, start, every, values, errors)
        if errors:
            rows = listing_rows(s)
            return render(request, "listings.html", status_code=400, rows=rows, values=values, errors=errors)
        s.add(Listing(**{k: values[k] for k in ("name", "keywords", "location", "radius_km")}, schedule=text))
        s.commit()
    return RedirectResponse("/listings", status_code=303)


@router.get("/listings/{listing_id}/edit", response_class=HTMLResponse)
def edit_listing_page(request: Request, listing_id: int):
    with session(request) as s:
        listing = _get_listing(s, listing_id)
    values = {"name": listing.name, "keywords": listing.keywords, "location": listing.location,
              "radius_km": listing.radius_km, **form_values(listing.schedule)}
    return render(request, "listing_form.html", listing=listing, values=values, errors={})


@router.post("/listings/{listing_id}", response_class=HTMLResponse)
def update_listing(request: Request, listing_id: int, name: str = Form(""), keywords: str = Form(""),
                   location: str = Form(""), radius_km: str = Form(""), schedule: str = Form("default"),
                   times: list[str] = Form([]), start: str = Form(""), every: str = Form("")):
    form = {"name": name, "keywords": keywords, "location": location, "radius_km": radius_km}
    with session(request) as s:
        listing = _get_listing(s, listing_id)
        values, errors = validate_listing_form(s, form, listing_id)
        text = _schedule(schedule, times, start, every, values, errors)
        if errors:
            return render(request, "listing_form.html", status_code=400, listing=listing,
                          values=values, errors=errors)
        if any(getattr(listing, key) != values[key] for key in ("keywords", "location", "radius_km")):
            listing.search_changed_at = utcnow()
        if text != listing.schedule:  # new times: wait for the next one, don't run for one that just passed
            listing.schedule, listing.schedule_changed_at = text, utcnow()
        for key in ("name", "keywords", "location", "radius_km"):
            setattr(listing, key, values[key])
        s.add(listing)
        s.commit()
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/toggle", response_class=HTMLResponse)
def toggle_listing(request: Request, listing_id: int):
    with session(request) as s:
        listing = _get_listing(s, listing_id)
        listing.active = not listing.active
        if listing.active:
            listing.schedule_changed_at = utcnow()  # switched back on: from its next time, no catching up
        s.add(listing)
        s.commit()
        row = _row(s, listing_id)
    return render(request, "_listing_row.html", row=row)


@router.post("/listings/{listing_id}/delete")
def remove_listing(request: Request, listing_id: int):
    with session(request) as s:
        _get_listing(s, listing_id)
        delete_listing(s, listing_id)
    return RedirectResponse("/listings", status_code=303)


@router.post("/listings/{listing_id}/results/delete")
def delete_results(request: Request, listing_id: int, next: str = Form("/")):
    """The × on a listing's tag in the report: its undecided results go, the listing stays."""
    with session(request) as s:
        _get_listing(s, listing_id)
        deleted = delete_listing_results(s, listing_id)
    # Back to the page it came from, never to another site ("//host" and "/\\host" lead elsewhere).
    back = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    return RedirectResponse(f"{back}{'&' if '?' in back else '?'}deleted={deleted}", status_code=303)
