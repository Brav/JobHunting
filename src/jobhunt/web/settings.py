"""Settings: the eligibility profile — where the user may work and live, and which languages they work in."""

from dataclasses import replace
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.eligibility import DEFAULT_PROFILE, Profile, load_profile, profile_saved, refresh_all, save_profile
from jobhunt.preferences import SOURCES, Preferences, load_preferences, save_preferences
from jobhunt.models import BlockedCompany, NegativeRule
from jobhunt.places import COUNTRIES
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
          prefs: Preferences | None = None, pref_errors: list[str] | None = None):
    with session(request) as s:
        blocked = list(s.exec(select(BlockedCompany).order_by(BlockedCompany.name)).all())
        rules = list(s.exec(select(NegativeRule).order_by(NegativeRule.phrase)).all())
        prefs = prefs or load_preferences(s)
    return render(request, "settings.html", status_code=status_code, active="settings", profile=profile,
                  blocked=blocked, rules=rules, prefs=prefs, pref_errors=pref_errors or [], sources=SOURCES,
                  languages=_form_languages(profile.languages), errors=errors, saved=saved,
                  regions=REGION_CHOICES, language_choices=LANGUAGE_CHOICES, contract_choices=CONTRACT_CHOICES,
                  countries=COUNTRY_CHOICES, zones=ZONES)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    with session(request) as s:
        # Nothing saved yet: the neutral profile, in the zone the app runs in (TZ, else UTC).
        profile = load_profile(s) if profile_saved(s) else replace(DEFAULT_PROFILE,
                                                                     time_zone=request.app.state.settings.tz.key)
    return _page(request, profile, [], saved if saved.isdecimal() else "")


@router.post("/settings", response_class=HTMLResponse)
def save_settings(request: Request, work_rights: list[str] = Form([]), home_country: str = Form(""),
                  time_zone: str = Form(""), relocate_to: list[str] = Form([]), languages: list[str] = Form([]),
                  contracts: list[str] = Form([])):
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
    if profile.home_country not in COUNTRIES:
        errors.append("Unknown country")
    try:
        ZoneInfo(profile.time_zone)
    except Exception:
        errors.append("Unknown time zone")
    if errors:
        return _page(request, profile, errors, status_code=422)
    with session(request) as s:
        save_profile(s, profile, utcnow())
        checked = refresh_all(s, profile)
    apply_time_zone(request.app, ZoneInfo(profile.time_zone))
    return RedirectResponse(f"/settings?saved={checked}", status_code=303)


def _int(value: str, low: int, high: int, message: str, errors: list[str], default: int) -> int:
    if value.strip().isdecimal() and low <= int(value) <= high:
        return int(value)
    errors.append(message)
    return default


@router.post("/settings/preferences", response_class=HTMLResponse)
def save_preferences_form(request: Request, skills: str = Form(""), follow_up_days: str = Form("7"),
                          auto_close_days: str = Form("30"), goal_applications_per_week: str = Form("5"),
                          goal_reviews_per_week: str = Form("50"), paused_until: str = Form(""),
                          sources: list[str] = Form([]), ntfy_url: str = Form("")):
    from datetime import date
    errors: list[str] = []
    with session(request) as s:
        current = load_preferences(s)
        profile = load_profile(s)
    paused = paused_until.strip()
    try:
        if paused:
            date.fromisoformat(paused)
    except ValueError:
        errors.append("Enter the pause date as a date")
    url = ntfy_url.strip()
    if url and not url.startswith(("https://", "http://")):
        errors.append("The push address must start with https://")
    prefs = replace(
        current, skills=tuple(dict.fromkeys(s.strip() for s in skills.split(",") if s.strip()))[:100],
        follow_up_days=_int(follow_up_days, 1, 90, "Follow-up days must be between 1 and 90", errors, current.follow_up_days),
        auto_close_days=_int(auto_close_days, 7, 365, "Auto-close days must be between 7 and 365", errors,
                             current.auto_close_days),
        goal_applications_per_week=_int(goal_applications_per_week, 0, 500, "Goals must be between 0 and 500", errors,
                                        current.goal_applications_per_week),
        goal_reviews_per_week=_int(goal_reviews_per_week, 0, 5000, "Goals must be between 0 and 5000", errors,
                                   current.goal_reviews_per_week),
        paused_until=paused, sources=tuple(x for x in SOURCES if x in sources), ntfy_url=url)
    if errors:
        return _page(request, profile, [], status_code=422, prefs=prefs, pref_errors=errors)
    with session(request) as s:
        save_preferences(s, prefs, utcnow())
        if prefs.skills != current.skills:
            from jobhunt import checks
            checks.refresh_fit(s)
    return RedirectResponse("/settings?prefs=saved#preferences", status_code=303)
