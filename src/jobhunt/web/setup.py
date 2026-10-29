"""The setup page a new install opens on: the user's situation, their CV and a first search, in one form."""

from dataclasses import replace

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.cv import MAX_CV_BYTES, CVError, check_upload, current_cv, store_upload
from jobhunt.eligibility import DEFAULT_PROFILE, Profile, load_profile, profile_saved
from jobhunt.models import Listing
from jobhunt.setup import fill_skills_from_cv, setup_state, skip_setup
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session
from jobhunt.web.listings import validate_listing_form
from jobhunt.web.settings import (CONTRACT_CHOICES, COUNTRY_CHOICES, LANGUAGE_CHOICES, REGION_CHOICES, ZONES,
                                  _form_languages, apply_profile, profile_from_form)

router = APIRouter()

EMPTY_SEARCH = {"keywords": "", "location": "", "radius_km": "25"}


def _page(request: Request, profile: Profile, *, detect_zone: bool, errors: list[str] | None = None,
          search: dict | None = None, search_errors: dict | None = None, cv_error: str = "", status_code: int = 200):
    with session(request) as s:
        state = setup_state(s)
        cv = current_cv(s, request.app.state.settings)
    return render(request, "setup.html", status_code=status_code, profile=profile, detect_zone=detect_zone,
                  pending=state == "pending", done=state == "done", errors=errors or [],
                  search=search or EMPTY_SEARCH, search_errors=search_errors or {}, cv=cv, cv_error=cv_error,
                  languages=_form_languages(profile.languages), regions=REGION_CHOICES,
                  language_choices=LANGUAGE_CHOICES, contract_choices=CONTRACT_CHOICES, countries=COUNTRY_CHOICES,
                  zones=ZONES)


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    with session(request) as s:
        saved = profile_saved(s)
        profile = load_profile(s)
    if saved:
        return _page(request, profile, detect_zone=False)
    # Nothing saved: the browser fills in its own time zone; the server's is the placeholder.
    return _page(request, replace(DEFAULT_PROFILE, time_zone=request.app.state.settings.tz.key), detect_zone=True)


@router.post("/setup", response_class=HTMLResponse)
def save_setup(request: Request, work_rights: list[str] = Form([]), home_country: str = Form(""),
               time_zone: str = Form(""), relocate_to: list[str] = Form([]), languages: list[str] = Form([]),
               contracts: list[str] = Form([]), keywords: str = Form(""), location: str = Form(""),
               radius_km: str = Form("25"), cv: UploadFile | None = File(None)):
    """Check everything first, then save: CV, profile (every job checked again), first search — or nothing."""
    settings = request.app.state.settings
    profile, errors = profile_from_form(work_rights, home_country, time_zone, relocate_to, languages, contracts)
    search = {"keywords": keywords.strip(), "location": location.strip(), "radius_km": radius_km.strip() or "25"}
    upload, cv_error = None, ""
    with session(request) as s:
        if cv is not None and cv.filename and current_cv(s, settings).origin != "env":
            content = cv.file.read(MAX_CV_BYTES + 1)
            try:
                check_upload(cv.filename, content)
                upload = (cv.filename, content)
            except CVError as e:
                cv_error = str(e)
        listing, search_errors = (validate_listing_form(s, {**search, "name": search["keywords"]})
                                  if search["keywords"] else (None, {}))
    if errors or cv_error or search_errors:
        return _page(request, profile, detect_zone=False, errors=errors, search=search, search_errors=search_errors,
                     cv_error=cv_error, status_code=422)
    if upload is not None:
        with session(request) as s:
            store_upload(s, settings.db_path.parent, *upload, utcnow())
    apply_profile(request, profile)
    with session(request) as s:
        fill_skills_from_cv(s, current_cv(s, settings).path)
        if listing is not None:
            s.add(Listing(**listing))
            s.commit()
    if listing is not None:
        request.app.state.coordinator.trigger("setup")
    return RedirectResponse("/", status_code=303)


@router.post("/setup/skip")
def skip(request: Request):
    with session(request) as s:
        if setup_state(s) == "pending":
            skip_setup(s, utcnow())
    return RedirectResponse("/", status_code=303)
