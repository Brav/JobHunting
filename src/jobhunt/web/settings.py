"""Settings: the eligibility profile — where the user may work and live, and which languages they work in."""

from dataclasses import replace
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.cv import MAX_CV_BYTES, CVError, current_cv, store_upload
from jobhunt.deepl_key import load_key, mask, remove_key, save_key
from jobhunt.eligibility import DEFAULT_PROFILE, Profile, load_profile, profile_saved, refresh_all, save_profile
from jobhunt.preferences import Preferences, load_preferences, save_preferences
from jobhunt.models import BlockedCompany, NegativeRule
from jobhunt.places import COUNTRIES
from jobhunt.schedule import form_values, from_form, to_text
from jobhunt.scheduler import default_schedule
from jobhunt.sites import BUILTIN
from jobhunt.setup import fill_skills_from_cv
from jobhunt.timeutil import utcnow
from jobhunt.web.common import apply_time_zone, render, session

router = APIRouter()

REGION_CHOICES = [("EU", "EU, EEA and Switzerland"), ("UK", "United Kingdom"), ("US", "United States"),
                  ("CA", "Canada"), ("AU", "Australia")]
SR_GROUP = ("sr", "hr", "bs", "me")  # one choice in the form: one language for job requirements
LANGUAGE_CHOICES = [("en", "English"), ("sr-group", "Serbian / Croatian / Bosnian / Montenegrin"), ("de", "German"),
                    ("fr", "French"), ("es", "Spanish"), ("it", "Italian"), ("nl", "Dutch"), ("pl", "Polish"),
                    ("pt", "Portuguese"), ("hu", "Hungarian"), ("ro", "Romanian"), ("sl", "Slovenian"),
                    ("bg", "Bulgarian"), ("uk", "Ukrainian"), ("ru", "Russian")]
CONTRACT_CHOICES = [("employment", "Employment"), ("b2b", "B2B / contractor"), ("eor", "Employer of record")]
COUNTRY_CHOICES = sorted(((code, name.removeprefix("the ")) for code, (name, _) in COUNTRIES.items()),
                         key=lambda c: c[1])
ZONES = sorted(z for z in available_timezones() if "/" in z) + ["UTC"]


def _form_languages(languages: tuple[str, ...]) -> list[str]:
    chosen = [code for code in languages if code not in SR_GROUP]
    return chosen + ["sr-group"] if any(code in SR_GROUP for code in languages) else chosen


def _page(request: Request, profile: Profile, errors: list[str], saved: str = "", status_code: int = 200,
          prefs: Preferences | None = None, pref_errors: list[str] | None = None, cv_notice: str = "",
          cv_error: str = "", translation_notice: str = "", translation_error: str = "",
          sched: dict | None = None, sched_error: str = "", schedule_notice: str = ""):
    with session(request) as s:
        blocked = list(s.exec(select(BlockedCompany).order_by(BlockedCompany.name)).all())
        rules = list(s.exec(select(NegativeRule).order_by(NegativeRule.phrase)).all())
        prefs = prefs or load_preferences(s)
        cv = current_cv(s, request.app.state.settings)
        default = default_schedule(load_preferences(s), request.app.state.settings)
    return render(request, "settings.html", status_code=status_code, active="settings", profile=profile,
                  cv=cv, cv_notice=cv_notice, cv_error=cv_error, translation=_translation(request),
                  sched=sched or form_values(to_text(default)), sched_error=sched_error,
                  schedule_notice=schedule_notice,
                  translation_notice=translation_notice, translation_error=translation_error,
                  blocked=blocked, rules=rules, prefs=prefs, pref_errors=pref_errors or [], builtin_sites=list(BUILTIN.values()),
                  languages=_form_languages(profile.languages), errors=errors, saved=saved,
                  regions=REGION_CHOICES, language_choices=LANGUAGE_CHOICES, contract_choices=CONTRACT_CHOICES,
                  countries=COUNTRY_CHOICES, zones=ZONES)


def _shown_profile(request: Request) -> Profile:
    """The saved profile; nothing saved yet: the neutral one, in the zone the app runs in (TZ, else UTC)."""
    with session(request) as s:
        if profile_saved(s):
            return load_profile(s)
    return replace(DEFAULT_PROFILE, time_zone=request.app.state.settings.tz.key)


def _translation(request: Request) -> dict:
    """Where the DeepL key comes from: "env" (.env wins), "saved" (Settings) or "" (none), and its masked form."""
    settings = request.app.state.settings
    if settings.deepl_auth_key:
        return {"origin": "env", "masked": ""}
    key = load_key(settings.db_path.parent)
    return {"origin": "saved" if key else "", "masked": mask(key) if key else ""}


TRANSLATION_NOTICES = {"saved": "Key saved · translation is on", "removed": "Key removed · translation is off"}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", cv: str = "", translation: str = "", schedule: str = ""):
    return _page(request, _shown_profile(request), [], saved if saved.isdecimal() else "",
                 cv_notice="CV saved" if cv == "saved" else "", translation_notice=TRANSLATION_NOTICES.get(translation, ""),
                 schedule_notice="Schedule saved" if schedule == "saved" else "")


@router.post("/settings/schedule", response_class=HTMLResponse)
def save_schedule(request: Request, schedule: str = Form("every"), times: list[str] = Form([]), start: str = Form(""),
                  every: str = Form("")):
    """The default search schedule: every listing without its own times follows it."""
    choice = schedule if schedule in ("times", "every") else "every"
    text, problems = from_form(choice, times, start, every)
    if problems:
        return _page(request, _shown_profile(request), [], status_code=422, sched_error=" ".join(problems),
                     sched={"choice": choice, "times": times or [""], "start": start, "every": every})
    now = utcnow()
    with session(request) as s:
        save_preferences(s, replace(load_preferences(s), schedule=text,
                                    schedule_changed_at=now.isoformat(timespec="minutes")), now)
    request.app.state.coordinator.schedule_changed()  # the phone push follows the new start
    return RedirectResponse("/settings?schedule=saved", status_code=303)


@router.post("/settings/translation", response_class=HTMLResponse)
def save_translation(request: Request, action: str = Form(""), key: str = Form("")):
    settings = request.app.state.settings
    if settings.deepl_auth_key:
        raise HTTPException(status_code=409, detail="The DeepL key comes from .env")
    data_dir = settings.db_path.parent
    try:
        if action == "remove":
            remove_key(data_dir)
        else:
            save_key(data_dir, key)
    except ValueError as e:
        return _page(request, _shown_profile(request), [], status_code=422, translation_error=str(e))
    except OSError as e:
        return _page(request, _shown_profile(request), [], status_code=422,
                     translation_error=f"Couldn't save the key: {e.strerror or e}")
    request.app.state.translator.set(request.app.state.translator_factory(load_key(data_dir)))
    return RedirectResponse(f"/settings?translation={'removed' if action == 'remove' else 'saved'}", status_code=303)


@router.post("/settings/cv", response_class=HTMLResponse)
def upload_cv(request: Request, cv: UploadFile = File(...)):
    settings = request.app.state.settings
    with session(request) as s:
        if current_cv(s, settings).origin == "env":
            raise HTTPException(status_code=409, detail="The CV comes from your CV folder (set in .env)")
        try:
            path = store_upload(s, settings.db_path.parent, cv.filename or "", cv.file.read(MAX_CV_BYTES + 1), utcnow())
        except CVError as e:
            return _page(request, _shown_profile(request), [], status_code=422, cv_error=str(e))
        fill_skills_from_cv(s, path)
    return RedirectResponse("/settings?cv=saved", status_code=303)


def profile_from_form(work_rights: list[str], home_country: str, time_zone: str, relocate_to: list[str],
                      languages: list[str], contracts: list[str]) -> tuple[Profile, list[str]]:
    """The profile the form describes, and what's wrong with it (Settings and the setup page share this)."""
    regions = dict(REGION_CHOICES)
    spoken = [code for choice in languages for code in (SR_GROUP if choice == "sr-group" else (choice,))]
    profile = Profile(
        work_rights=tuple(r for r in dict.fromkeys(work_rights) if r in regions), home_country=home_country.strip(),
        time_zone=time_zone.strip(), relocate_to=tuple(r for r in dict.fromkeys(relocate_to) if r in regions),
        languages=tuple(dict.fromkeys(c for c in spoken if c in dict(LANGUAGE_CHOICES) or c in SR_GROUP)),
        contracts=tuple(c for c in dict.fromkeys(contracts) if c in dict(CONTRACT_CHOICES)))
    errors = []
    if not profile.languages:
        errors.append("Pick at least one language")
    if not profile.home_country:
        errors.append("Choose your country")
    elif profile.home_country not in COUNTRIES:
        errors.append("Unknown country")
    try:
        ZoneInfo(profile.time_zone)
    except Exception:
        errors.append("Unknown time zone")
    return profile, errors


def apply_profile(request: Request, profile: Profile) -> int:
    """Save the profile, check every job with it, and move to its time zone; returns how many jobs were checked."""
    with session(request) as s:
        save_profile(s, profile, utcnow())
        checked = refresh_all(s, profile)
    apply_time_zone(request.app, ZoneInfo(profile.time_zone))
    return checked


@router.post("/settings", response_class=HTMLResponse)
def save_settings(request: Request, work_rights: list[str] = Form([]), home_country: str = Form(""),
                  time_zone: str = Form(""), relocate_to: list[str] = Form([]), languages: list[str] = Form([]),
                  contracts: list[str] = Form([])):
    profile, errors = profile_from_form(work_rights, home_country, time_zone, relocate_to, languages, contracts)
    if errors:
        return _page(request, profile, errors, status_code=422)
    return RedirectResponse(f"/settings?saved={apply_profile(request, profile)}", status_code=303)


def _int(value: str, low: int, high: int, message: str, errors: list[str], default: int) -> int:
    if value.strip().isdecimal() and low <= int(value) <= high:
        return int(value)
    errors.append(message)
    return default


@router.post("/settings/preferences", response_class=HTMLResponse)
def save_preferences_form(request: Request, skills: str = Form(""), follow_up_days: str = Form("7"),
                          auto_close_days: str = Form("30"), goal_applications_per_week: str = Form("5"),
                          goal_reviews_per_week: str = Form("50"), paused_until: str = Form(""),
                          sites: list[str] = Form([]), ntfy_url: str = Form("")):
    from datetime import date
    errors: list[str] = []
    with session(request) as s:
        current = load_preferences(s)
    profile = _shown_profile(request)
    paused = paused_until.strip()
    try:
        if paused:
            date.fromisoformat(paused)
    except ValueError:
        errors.append("Enter the pause date as a date")
    url = ntfy_url.strip()
    if url and not url.startswith(("https://", "http://")):
        errors.append("The push address must start with https://")
    chosen = tuple(key for key in BUILTIN if key in sites)
    if not chosen:
        errors.append("Tick at least one default site.")
    prefs = replace(
        current, skills=tuple(dict.fromkeys(s.strip() for s in skills.split(",") if s.strip()))[:100],
        follow_up_days=_int(follow_up_days, 1, 90, "Follow-up days must be between 1 and 90", errors, current.follow_up_days),
        auto_close_days=_int(auto_close_days, 7, 365, "Auto-close days must be between 7 and 365", errors,
                             current.auto_close_days),
        goal_applications_per_week=_int(goal_applications_per_week, 0, 500, "Goals must be between 0 and 500", errors,
                                        current.goal_applications_per_week),
        goal_reviews_per_week=_int(goal_reviews_per_week, 0, 5000, "Goals must be between 0 and 5000", errors,
                                   current.goal_reviews_per_week),
        paused_until=paused, sites=chosen or current.sites, ntfy_url=url)
    if errors:
        return _page(request, profile, [], status_code=422, prefs=prefs, pref_errors=errors)
    with session(request) as s:
        save_preferences(s, prefs, utcnow())
        if prefs.skills != current.skills:
            from jobhunt import checks
            checks.refresh_fit(s)
    return RedirectResponse("/settings?prefs=saved#preferences", status_code=303)
