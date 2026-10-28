"""Settings: the eligibility profile — where the user may work and live, and which languages they work in."""

from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from jobhunt.eligibility import Profile, load_profile, refresh_all, save_profile
from jobhunt.models import BlockedCompany, NegativeRule
from jobhunt.places import COUNTRIES
from jobhunt.timeutil import utcnow
from jobhunt.web.common import render, session

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
ZONES = sorted(z for z in available_timezones() if z.startswith("Europe/"))


def _form_languages(languages: tuple[str, ...]) -> list[str]:
    chosen = [code for code in languages if code not in SR_GROUP]
    return chosen + ["sr-group"] if any(code in SR_GROUP for code in languages) else chosen


def _page(request: Request, profile: Profile, errors: list[str], saved: str = "", status_code: int = 200):
    with session(request) as s:
        blocked = list(s.exec(select(BlockedCompany).order_by(BlockedCompany.name)).all())
        rules = list(s.exec(select(NegativeRule).order_by(NegativeRule.phrase)).all())
    return render(request, "settings.html", status_code=status_code, active="settings", profile=profile,
                  blocked=blocked, rules=rules,
                  languages=_form_languages(profile.languages), errors=errors, saved=saved,
                  regions=REGION_CHOICES, language_choices=LANGUAGE_CHOICES, contract_choices=CONTRACT_CHOICES,
                  countries=COUNTRY_CHOICES, zones=ZONES)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    with session(request) as s:
        profile = load_profile(s)
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
    return RedirectResponse(f"/settings?saved={checked}", status_code=303)
