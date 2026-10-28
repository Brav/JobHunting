"""Can this user take this job? Rules over a job's location and texts, each verdict with the sentence behind it.

The rules read the English text (the translation when there is one) and, for language requirements, the original.
Each phrase family below is a named list with an example, so a miss found by an override can be added by hand.
"""

import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, or_, select

from jobhunt.language import language_name
from jobhunt.models import Job, Setting
from jobhunt.places import REGIONS, place_name, place_of, places_in

VERDICTS = ("eligible", "relocate", "check", "not_eligible")
VERDICT_LABELS = {"eligible": "Eligible", "relocate": "Relocate", "check": "Check", "not_eligible": "Not eligible"}
VERDICT_SYMBOLS = {"eligible": "✓", "relocate": "↗", "check": "?", "not_eligible": "✕"}
RULES_VERSION = 4  # bump when the rules change: every job is checked again on the next start


@dataclass(frozen=True)
class Profile:
    work_rights: tuple[str, ...] = ("EU",)  # keys of places.REGIONS
    home_country: str = "RS"
    time_zone: str = "Europe/Belgrade"
    relocate_to: tuple[str, ...] = ("EU",)
    languages: tuple[str, ...] = ("en", "sr", "hr", "bs", "me")
    contracts: tuple[str, ...] = ("employment", "b2b", "eor")


DEFAULT_PROFILE = Profile()


@dataclass(frozen=True)
class Assessment:
    verdict: str
    reasons: list[dict]  # {"kind", "text", "quote"}; the first one decided the verdict


_PLACE = r"(?P<place>[^.;:\n()|*!?]{1,80})"  # the words after "based in", read by places.places_in

# "must be based in Portugal", "candidates residing in Spain", "EU-based applicants", "anywhere in Europe",
# "remote within Germany", "we hire in Poland, Serbia and Romania"
RESIDENCE = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:must|should|need to|needs to|required to|have to|has to|will need to)\s+(?:be\s+)?(?:currently\s+)?"
    r"(?:based|located|living|residing|reside|live|resident)\s+(?:in|within)\s+" + _PLACE,
    r"\b(?:candidates|applicants|people|engineers|developers)\s+(?:who\s+are\s+)?(?:based|located|residing|living)\s+"
    r"(?:in|within)\s+" + _PLACE,
    r"\b(?:remote|fully remote|100% remote|work remotely)\s+(?:within|in|from|across)\s+" + _PLACE,
    r"\b(?:remote|fully remote|100% remote)\s*(?:\(|:|,|\s[-–—]\s)\s*" + _PLACE,  # "Remote (Europe)", "Remote - EMEA"
    r"\b(?P<anywhere>anywhere)\s+(?:in|within|across)\s+" + _PLACE,
    r"\b(?:based|located)\s+in\s+(?P<place>[^.;:\n()|*!?]{1,40}?)\s+only\b",
    r"\b(?:hire|hiring|recruit|recruiting|accepting candidates|open to candidates)\s+(?:remotely\s+)?(?:in|from|across)\s+"
    + _PLACE,
    r"\b(?:tax\s+)?resident\s+(?:in|of)\s+" + _PLACE,
)] + [re.compile(p) for p in (  # capitalised names only: "EU-based", "UK / EU based", "Lisbon-based candidates"
    r"(?P<place>\b[A-Z][\w.]*(?:(?:\s*(?:/|,|or|and)\s*|\s+)[A-Z][\w.]*){0,3})[- ]based\b(?!\s+(?:company|companies|startup|team|"
    r"teams|business|firm|agency|clients?|customers?|organi[sz]ation|SaaS|solutions?|platform|products?|services?|approach)\b)",
    r"\b(?P<place>EU|EEA|UK|US)\s+residents?\b",
)]
# A job title ending in a region: "Full Stack Engineer - EMEA", "Backend Developer (Europe)"
TITLE_REGION = re.compile(r"[-–—|(]\s*(?P<place>EMEA|Europe|European Union|EU)\s*\)?\s*$", re.IGNORECASE)
# Where a list of places ends: "Spain to support our European customers", "Portugal and willing to travel", "home, with trips"
CLAUSE_END = re.compile(r"(?:^|\s+)(?:with|to|who|for|where|while|but|so|as|since|because|when|that|which|will|would|should|must|"
                        r"may|can|could|is|are|and\s+(?:be|have|willing|able|speak))\b|,\s*(?!(?:and|or)\b)(?-i:[a-z])",
                        re.IGNORECASE)
# … and where it turns out to be an option, not a rule: "candidates living in London may use our office"
OPTIONAL = re.compile(r"\s+(?:may|can|could|are\s+(?:also\s+)?welcome)\b", re.IGNORECASE)
# "right to work in the EU", "eligible to work in the UK", "work permit for Germany"
WORK_RIGHTS = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:right|eligible|eligibility|authori[sz]ed|authori[sz]ation|permitted|entitled)\s+to\s+(?:live\s+and\s+)?work\s+"
    r"(?:in|within)\s+"
    + _PLACE,
    r"\bwork\s+(?:permit|authori[sz]ation|visa)\s+(?:for|in)\s+" + _PLACE,
)]
NATIONALITIES = {"British": "British", "UK": "British", "US": "US", "American": "US", "German": "German",
                 "French": "French", "Dutch": "Dutch", "Spanish": "Spanish", "Italian": "Italian", "Polish": "Polish",
                 "Portuguese": "Portuguese", "Irish": "Irish", "Swiss": "Swiss", "Austrian": "Austrian",
                 "Canadian": "Canadian", "Australian": "Australian"}
# "must be a German citizen", "British citizenship required"
CITIZENSHIP = [re.compile(p) for p in (
    r"\b(?:[Mm]ust be|[Oo]nly)\s+(?:an?\s+)?(?P<nat>[A-Z][A-Za-z]+)\s+(?:citizens?|nationals?)\b",
    r"\b(?P<nat>[A-Z][A-Za-z]+)\s+citizenship\s+(?:is\s+)?(?:required|mandatory)",
)]
CLEARANCE = [re.compile(r"\bsecurity\s+clearance\b", re.IGNORECASE), re.compile(r"\b(?:SC|DV|NV1|NV2)[- ](?:clearance|cleared)\b")]
# "work from almost anywhere in the world", "remote worldwide", "fully distributed" — a bare "work from anywhere"
# names no scope and is usually a perk, so it doesn't count
WORLDWIDE = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:anywhere\s+in\s+the\s+world|from\s+any\s+country|fully\s+distributed|location[- ]independent)\b",
    r"\b(?:remote|hire|hiring|candidates?|applicants?|position|role)\b[^.\n]{0,30}\b(?:worldwide|world-wide|globally)\b",
)]
# A sentence about time-limited remote work is a perk, not where you may live: "work from anywhere for 30 days per year"
PERK = re.compile(r"\b(?:workation|\d+\s+(?:days?|weeks?)\s+(?:per|a|each|every)\s+year|up\s+to\s+\d+\s+(?:days?|weeks?)|"
                  r"(?:days?|weeks?)\s+(?:per|a)\s+year)\b", re.IGNORECASE)
# "remote work within the country of employment" takes back a "work from anywhere" in the same sentence
COUNTRY_OF_EMPLOYMENT = re.compile(r"\b(?:within|in)\s+(?:the|your)\s+country\s+of\s+(?:employment|residence)\b|"
                                   r"\bwithin\s+your\s+country\b", re.IGNORECASE)
# "European time zones", "EU timezone", "CET ± 2", "within 3 hours of CET", "overlap … (CET, UTC+1"
TZ_WINDOW = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:European|EU|CET|CEST|EET|Central European)\s+time\s?zones?\b",
    r"\b(?:CET|CEST|UTC|GMT)\s*(?:[+-]\s*\d{1,2}\s*)?(?:±|\+/-|\+-)\s*\d",
    r"\bwithin\s+\d+\s+hours?\s+of\s+(?:CET|CEST|UTC|GMT)",
    r"\boverlap\w*\b[^.\n]{0,100}?\b(?:CET|CEST|Central European|European (?:working )?hours|UTC\s*\+\s*[12]\b|"
    r"GMT\s*\+\s*[12]\b)",
)]
TZ_RANGE = re.compile(r"\b(?:UTC|GMT)\s*([+\-−]\s*\d{1,2})\s*(?:to|-|–|and)\s*(?:UTC|GMT)\s*([+\-−]\s*\d{1,2})", re.IGNORECASE)
# "overlap with US Eastern hours", "PST hours", "Pacific Time"
US_HOURS = [re.compile(r"\b(?:US|U\.S\.|American|North American)\s+(?:business\s+|working\s+|Eastern\s+|Pacific\s+|"
                       r"Central\s+)?(?:hours|time\s?zones?)\b"),
            re.compile(r"\b(?:EST|EDT|PST|PDT|CST|CDT|ET|PT)\s+(?:business\s+|working\s+)?hours\b"),
            re.compile(r"\b(?:Eastern|Pacific|Central|Mountain)\s+(?:Standard\s+)?Time\b"),
            re.compile(r"\boverlap\w*\s+with\s+(?:US|U\.S\.|North America|EST|PST|ET|PT|Eastern|Pacific)\b", re.IGNORECASE)]
# "under a B2B contract", "umowa B2B", "as a contractor" — not "B2B SaaS"
B2B = [re.compile(r"\b(?:B2B\s+(?:contract|agreement|basis|cooperation|contractor)|on\s+a\s+B2B\s+basis|umow[aęy]\s+B2B)\b",
                  re.IGNORECASE),
       re.compile(r"\b(?:as\s+an?\s+(?:independent\s+)?contractor|contractor\s+(?:role|position|basis|agreement)|"
                  r"freelance\s+(?:contract|basis))\b", re.IGNORECASE)]
# "Deel", "Oyster", "employer of record" — capitalised names only ("deel" is a Dutch word)
EOR = [re.compile(r"\b(?:Deel|Oyster(?:HR)?|EOR)\b"), re.compile(r"\b(?:employer\s+of\s+record|Remote\.com)\b", re.IGNORECASE)]

LANGUAGES = {"German": "de", "French": "fr", "Spanish": "es", "Italian": "it", "Dutch": "nl", "Flemish": "nl",
             "Polish": "pl", "Portuguese": "pt", "Swedish": "sv", "Danish": "da", "Norwegian": "no", "Finnish": "fi",
             "Czech": "cs", "Slovak": "sk", "Hungarian": "hu", "Romanian": "ro", "Greek": "el", "Lithuanian": "lt",
             "Latvian": "lv", "Estonian": "et", "Bulgarian": "bg", "Ukrainian": "uk", "Russian": "ru", "Turkish": "tr",
             "Croatian": "hr", "Serbian": "sr", "Bosnian": "bs", "Slovenian": "sl", "Hebrew": "he", "Arabic": "ar",
             "Japanese": "ja", "Chinese": "zh", "Mandarin": "zh", "English": "en"}
_L = r"(?P<lang>" + "|".join(LANGUAGES) + r")"
# "fluent German", "German (C1)", "native German speaker", "very good German skills", "German is required"
LANGUAGE_EN = [re.compile(p, re.IGNORECASE) for p in (
    r"\bfluen(?:t|cy)\s+(?:command\s+of\s+|level\s+)?(?:in\s+)?(?:(?:English|the\s+English\s+language)\s+(?:and|&|as\s+well\s+as)\s+)?"
    + _L + r"\b",
    r"\b" + _L + r"\s+\(?\s*(?:fluent|fluency|native|mother\s+tongue|business\s+fluent|C1|C2|B2)\b",
    r"(?<!React )(?<!cloud-)(?<!cloud )\b(?:native|mother[- ]tongue)\s+(?:level\s+)?(?:or\s+fluent\s+)?" + _L + r"\b",
    r"\b" + _L + r"\s+(?:at\s+|on\s+)?(?:an?\s+)?(?:level\s+)?(?:C1|C2|B2)\b",
    r"\bbusiness[- ](?:fluent|level)\s+" + _L + r"\b",
    r"\b(?:excellent|very\s+good|strong|good|solid|professional|fluent)\s+(?:written\s+and\s+(?:spoken|verbal)\s+|"
    r"spoken\s+and\s+written\s+)?" + _L + r"(?:\s+language)?\s+(?:skills|knowledge|proficiency|communication)\b",
    r"\b(?:command|knowledge|proficiency|fluency|communication)\s+(?:of|in)\s+(?:the\s+)?(?:English\s+(?:and|&)\s+)?"
    + _L + r"\b(?!\s+(?:market|customers?|clients?|law|legal|tax|system|culture|landscape|business|regulations?|"
    r"accounting|economy|healthcare|public|companies|data))",
    r"\b" + _L + r"(?:\s+language)?\s+(?:skills\s+)?(?:is\s+|are\s+)?(?:required|mandatory|essential|a\s+must)\b",
    r"\b(?:very\s+good|excellent|good|strong|fluent|solid)\s+" + _L + r"\s+(?:and|&)\s+(?:\w+\s+){0,2}English\s+"
    r"(?:language\s+)?(?:skills|knowledge)\b",
    r"\b(?:main|working|business|company|team|daily)\s+language\s+(?:is|will\s+be)\s+" + _L + r"\b",
    r"\b" + _L + r"[- ]speaking\s+(?:candidate|engineer|developer|person|professional)\b",
    r"\b" + _L + r"\s+speaker\b",
)]
# The same requirement written in the listing's own language (before or without a translation).
LANGUAGE_ORIGINAL = [
    ("de", re.compile(r"\b(?:Deutschkenntnisse|deutsche\s+Sprachkenntnisse|Deutsch\s*[(-]?\s*(?:C1|C2|B2|fließend|"
                      r"verhandlungssicher|Muttersprache)|(?:fließend\w*|verhandlungssicher\w*|sehr\s+gute\w*|"
                      r"exzellente\w*)\s+Deutsch\w*|Deutsch\s+als\s+Muttersprache|gute\w*\s+Deutsch-\s*(?:und|sowie)|"
                      r"Deutsch\w*\s+(?:auf\s+)?(?:Level|Niveau)\s+[BC][12]|Deutsch\s+beherrsch\w*|"
                      r"(?:sprichst|schreibst)\s+(?:und\s+(?:sprichst|schreibst)\s+)?(?:fließend\s+)?Deutsch|"
                      r"Kommunikations\w*\s+in\s+Deutsch)", re.IGNORECASE)),
    ("fr", re.compile(r"\b(?:maîtrise\s+(?:du|de\s+la\s+langue)\s+français\w*|français\s+(?:courant|natif|C1|C2|B2)|"
                      r"francophone)\b", re.IGNORECASE)),
    ("es", re.compile(r"\b(?:español\s+(?:nativo|fluido|C1|C2|B2)|nivel\s+(?:alto|avanzado|nativo)\s+de\s+español|"
                      r"dominio\s+del\s+español|castellano\s+nativo|fluidez\s+en\s+(?:inglés\s+y\s+)?español|"
                      r"domin\w*\s+(?:el\s+|del\s+)?español)\b", re.IGNORECASE)),
    ("it", re.compile(r"\b(?:italiano\s+(?:almeno\s+|minimo\s+)?(?:fluente|madrelingua|C1|C2|B2)|"
                      r"(?:ottima|buona)\s+conoscenza\s+(?:della\s+lingua\s+)?italiana|madrelingua\s+italian[ao])\b",
                      re.IGNORECASE)),
    ("nl", re.compile(r"\b(?:vloeiend\s+Nederlands|Nederlands\s+(?:vloeiend|C1|C2|B2)|uitstekende\s+beheersing\s+van\s+"
                      r"(?:de\s+)?Nederlandse?\s+taal|Nederlandstalig|beheers\w*\s+(?:het\s+)?Nederlands|"
                      r"(?:spreekt|schrijft)\s+(?:en\s+(?:spreekt|schrijft)\s+)?(?:uitstekend|vloeiend|goed)\s+Nederlands|"
                      r"(?:communicatie\w*|vaardigheden)\s+in\s+het\s+Nederlands)\b", re.IGNORECASE)),
    ("pl", re.compile(r"\b(?:biegł\w+\s+(?:znajomość\s+)?(?:języka\s+)?polsk\w+)\b", re.IGNORECASE)),
    ("pt", re.compile(r"\b(?:português\s+(?:fluente|nativo|C1|C2)|fluência\s+em\s+português)\b", re.IGNORECASE)),
]
POLISH_LANGUAGE = re.compile(r"\bznajomoś[cć]\s+języka\s+(?P<lang>niemieckiego|francuskiego|hiszpańskiego|włoskiego|"
                             r"niderlandzkiego)\b", re.IGNORECASE)
_POLISH = {"niemieckiego": "de", "francuskiego": "fr", "hiszpańskiego": "es", "włoskiego": "it", "niderlandzkiego": "nl"}
# A requirement in the same sentence as these is a wish, not a condition: "German is a plus".
NICE_TO_HAVE = re.compile(r"\b(?:plus|nice[- ]to[- ]have|advantage\w*|bonus|preferred|desirable|beneficial|welcome|"
                          r"von\s+Vorteil|wünschenswert|un\s+plus|apprécié\w*|valorable|se\s+valorará|deseable|"
                          r"preferibile|gradit\w|pluspunt|mile\s+widzian\w|atut\w*)\b", re.IGNORECASE)

# A requirement that applies only elsewhere: "French is required for positions located in Quebec".
CONDITIONAL = re.compile(r"\b(?:for\s+(?:positions|roles|candidates)\s+(?:\w+\s+){0,2}(?:located|based)\s+in|"
                         r"if\s+(?:you\s+are\s+)?(?:based|located)\s+in|"
                         r"(?:do\s+not|don't|doesn't|does\s+not)\s+(?:yet\s+)?(?:have|speak)|"  # "who do not have a good knowledge of"
                         r"training\s+(?:is\s+)?(?:offered|provided|available))\b", re.IGNORECASE)

# "No German required", "Keine Deutschkenntnisse", "Deutsch … nicht erforderlich": near the match, in the same clause
NEGATION = re.compile(r"\b(?:no|not|without|never|don't|do\s+not|does\s+not|doesn't|kein\w*|nicht|ohne|pas|sin|non|"
                      r"geen|niet)\b", re.IGNORECASE)
# "German or English": either will do, so neither is required
_ENGLISH = r"(?:English|Englisch\w*|anglais|ingl[eé]s|Engels|inglese)"
ALTERNATIVE_AFTER = re.compile(r"^\W{0,3}\s*(?:\(\s*[ABC][12]\s*\)\s*)?(?:or|oder|ou|o|of)\s+" + _ENGLISH + r"\b", re.IGNORECASE)
ALTERNATIVE_BEFORE = re.compile(_ENGLISH + r"\s+(?:or|oder|ou|o|of)\s+$", re.IGNORECASE)

_STANDING_ORDER = {"open": 0, "relocate": 1, "rights": 2, "closed": 3}


def _reason(kind: str, text: str, quote: str) -> dict:
    return {"kind": kind, "text": text, "quote": quote}


def _quote(text: str, start: int, end: int) -> str:
    """The sentence around a match, cleaned of Markdown, at most 200 characters."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n;") + 1
    rights = [i for i in (text.find(c, end) for c in ".!?\n;") if i != -1]
    right = min(rights) + 1 if rights else len(text)
    if right - left > 200:
        left = max(left, start - 80)
        right = min(right, left + 200)
    return re.sub(r"\s+", " ", re.sub(r"[*_`#|]+", "", text[left:right])).strip(" -•")


def _clause(text: str, start: int, end: int, before: int = 40, after: int = 30) -> tuple[str, str]:
    """The words just before and after a match, within its clause."""
    left = max([text.rfind(c, 0, start) for c in ".!?\n;:,"] + [start - before - 1]) + 1
    rights = [i for i in (text.find(c, end) for c in ".!?\n;:,") if i != -1] + [end + after]
    return text[left:start], text[end:min(rights)]


def _negated(text: str, start: int, end: int) -> bool:
    before, after = _clause(text, start, end)
    return bool(NEGATION.search(before) or NEGATION.search(after))


def _either_or_english(text: str, start: int, end: int) -> bool:
    return bool(ALTERNATIVE_AFTER.search(text[end:end + 40]) or ALTERNATIVE_BEFORE.search(text[max(0, start - 30):start]))


def _not_a_requirement(text: str, start: int, end: int) -> bool:
    return _sentence_is_a_wish(text, start, end) or _negated(text, start, end) or _either_or_english(text, start, end)


def _place_list(text: str) -> list[str]:
    """The places a rule names: the list right after "based in", up to where the sentence moves on."""
    cut = CLAUSE_END.search(text)
    if cut and OPTIONAL.match(text, cut.start()):
        return []  # "candidates living in London may use our office": an option, not a rule
    head = text[:cut.start()] if cut else text
    return places_in(re.sub(r"\bfrom\s+home\b|\bhome\b", " ", head))


def _sentence_is_a_wish(text: str, start: int, end: int) -> bool:
    """A wish ("German is a plus") or a condition that applies elsewhere ("… for positions located in Quebec")."""
    sentence = _quote(text, start, end)
    return bool(NICE_TO_HAVE.search(sentence) or CONDITIONAL.search(sentence))


def _countries(place: str) -> frozenset[str]:
    return REGIONS.get(place, frozenset({place}))


def standing(place: str, profile: Profile) -> str:
    """open (covers home), relocate (work rights and willing to move), rights (work rights only) or closed."""
    countries = _countries(place)
    if profile.home_country in countries:
        return "open"
    rights = set().union(*(REGIONS.get(r, ()) for r in profile.work_rights)) | {profile.home_country}
    if countries <= rights:
        movable = set().union(*(REGIONS.get(r, ()) for r in profile.relocate_to))
        return "relocate" if countries <= movable else "rights"
    return "closed"


def _best(places: list[str], profile: Profile, prefer: str | None = None) -> tuple[str, str]:
    """The place best for the user; among equals, the job's own country ("based in Spain or Poland" in Warsaw)."""
    return min(((standing(p, profile), p) for p in places), key=lambda sp: (_STANDING_ORDER[sp[0]], sp[1] != prefer))


def _is_perk(text: str, start: int, end: int) -> bool:
    return bool(PERK.search(_quote(text, start, end)))


def _home_offset(profile: Profile) -> float:
    try:
        return ZoneInfo(profile.time_zone).utcoffset(datetime(2026, 1, 15)).total_seconds() / 3600
    except Exception:
        return 1.0


def _tz_window_fits(text: str, profile: Profile) -> tuple[int, int] | None:
    """The span of a time-zone window that includes the user's time zone, if the text names one."""
    offset = _home_offset(profile)
    for m in TZ_RANGE.finditer(text):
        lo, hi = (int(g.replace("−", "-").replace(" ", "")) for g in m.groups())
        if min(lo, hi) <= offset <= max(lo, hi) and not _is_perk(text, *m.span()):
            return m.span()
    if 0 <= offset <= 3:  # named European windows (CET, "European time zones") fit Europe's own time zones
        for pattern in TZ_WINDOW:
            for m in pattern.finditer(text):
                if not _is_perk(text, *m.span()):
                    return m.span()
    return None


def _language_requirements(english: str, original: str) -> list[tuple[str, str]]:
    found = []
    for pattern in LANGUAGE_EN:
        for m in pattern.finditer(english):
            word = next(w for w in LANGUAGES if w.casefold() == m.group("lang").casefold())
            if not _not_a_requirement(english, *m.span()):
                found.append((LANGUAGES[word], _quote(english, *m.span())))
    for code, pattern in LANGUAGE_ORIGINAL:
        for m in pattern.finditer(original):
            if not _not_a_requirement(original, *m.span()):
                found.append((code, _quote(original, *m.span())))
    for m in POLISH_LANGUAGE.finditer(original):
        if not _not_a_requirement(original, *m.span()):
            found.append((_POLISH[m.group("lang").casefold()], _quote(original, *m.span())))
    return found


def _first_match(patterns, text: str):
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m
    return None


def assess(job, profile: Profile) -> Assessment:
    # Markdown emphasis splits phrases ("Sehr gute **Deutsch**-…"); it carries no meaning for the rules.
    english = re.sub(r"[*`]+", "", "\n".join(t for t in (job.title, job.description_en or job.description) if t))
    original = re.sub(r"[*`]+", "", job.description or "")
    home = place_name(profile.home_country)
    blockers, opens, relocates, downgrades, notes = [], [], [], [], []
    closed_residence = []  # "must be based in the UK": blocks only if no other residence rule lets the user in

    for code, quote in _language_requirements(english, original):
        if code not in profile.languages:
            blockers.append(_reason("blocker", f"Requires {language_name(code)}", quote))
    location = place_of(job.location)
    for pattern in RESIDENCE:
        for m in pattern.finditer(english):
            places = _place_list(m.group("place"))
            if not places or _is_perk(english, *m.span()):
                continue
            state, place = _best(places, profile, prefer=location)
            quote = _quote(english, *m.span())
            if state == "open":
                anywhere = "anywhere" in m.groupdict() and m.group("anywhere")
                label = f"anywhere in {place_name(place)}" if anywhere else place_name(place)
                opens.append(_reason("open", f"Open to {label}", quote))
            elif state == "relocate":
                relocates.append(_reason("relocate", f"Must be based in {place_name(place)}", quote))
            elif state == "closed":
                closed_residence.append(_reason("blocker", f"Must be based in {place_name(place)}", quote))
            else:
                notes.append(_reason("note", f"Must be based in {place_name(place)}", quote))
    if job.title and (m := TITLE_REGION.search(job.title)):
        state, place = _best(places_in(m.group("place")), profile)
        if state == "open":
            opens.append(_reason("open", f"Open to {place_name(place)}", job.title))
        elif state == "relocate":
            relocates.append(_reason("relocate", f"Must be based in {place_name(place)}", job.title))
    for pattern in WORK_RIGHTS:
        for m in pattern.finditer(english):
            places = _place_list(m.group("place"))
            if not places:
                continue
            state, place = _best(places, profile)
            quote = _quote(english, *m.span())
            if state == "closed":
                blockers.append(_reason("blocker", f"Requires work rights in {place_name(place)}", quote))
            else:
                have = "You have EU work rights" if place == "EU" else f"You have work rights in {place_name(place)}"
                notes.append(_reason("note", have, quote))
    for pattern in CITIZENSHIP:
        for m in pattern.finditer(english):
            if m.group("nat") in NATIONALITIES:
                blockers.append(_reason("blocker", f"Requires {NATIONALITIES[m.group('nat')]} citizenship",
                                        _quote(english, *m.span())))
    if (m := _first_match(CLEARANCE, english)) and not _negated(english, *m.span()):
        blockers.append(_reason("blocker", "Requires security clearance", _quote(english, *m.span())))
    for m in (m for pattern in WORLDWIDE for m in pattern.finditer(english)):
        quote = _quote(english, *m.span())
        if not PERK.search(quote) and not COUNTRY_OF_EMPLOYMENT.search(quote):
            opens.append(_reason("open", "Open worldwide", quote))
            break
    # A time-zone window says where you may work from, not that a job you must commute to is remote.
    if job.workplace not in ("hybrid", "on-site") and (span := _tz_window_fits(english, profile)):
        opens.append(_reason("open", "European time-zone window", _quote(english, *span)))
    if m := _first_match(US_HOURS, english):
        downgrades.append(_reason("note", "Works US hours", _quote(english, *m.span())))
    if "b2b" in profile.contracts and (m := _first_match(B2B, english)):
        notes.append(_reason("note", f"B2B contract — may allow working from {home}, ask", _quote(english, *m.span())))
    if "eor" in profile.contracts and (m := _first_match(EOR, english)):
        notes.append(_reason("note", f"Employer of record — may allow working from {home}, ask",
                             _quote(english, *m.span())))
    if job.language and job.language not in profile.languages:
        name = language_name(job.language)
        downgrades.append(_reason("note", f"Listing is in {name} — {name} may be required", job.title or ""))

    where = job.location or ""
    if location in ("EUROPE", "EMEA") and job.workplace == "remote":
        opens.append(_reason("open", f"Remote in {place_name(location)}", where))
    if location == profile.home_country:
        opens.append(_reason("open", f"Job in {home}", where))
    located = standing(location, profile) if location else None

    if not opens and not relocates:
        blockers += closed_residence  # "UK-based" in the title, "EU-based required" in the text: the EU rule wins
    if blockers:
        verdict, first = "not_eligible", blockers[0]
    elif opens:
        verdict, first = ("check", downgrades[0]) if downgrades else ("eligible", opens[0])
    elif relocates:  # a stated residence rule says more than where the employer happens to be
        verdict, first = "relocate", relocates[0]
    elif located == "closed":
        verdict, first = "not_eligible", _reason("blocker", f"Job in {place_name(location)} — no work rights there", where)
    elif location == "EU" and located == "relocate":
        verdict, first = "relocate", _reason("relocate", "Remote within the EU", where)
    elif job.workplace in ("hybrid", "on-site") and located == "relocate":
        kind = "Hybrid" if job.workplace == "hybrid" else "On-site"
        verdict, first = "relocate", _reason("relocate", f"{kind} in {place_name(location)}", where)
    elif job.workplace is None:
        verdict, first = "check", _reason("note", "Workplace unknown", where)
    elif location and location not in REGIONS:
        verdict, first = "check", _reason("note", f"Remote in {place_name(location)} — policy not stated", where)
    else:
        verdict, first = "check", _reason("note", "Location policy not stated", where)

    reasons, seen = [], set()
    for reason in (first, *blockers, *opens, *relocates, *closed_residence, *downgrades, *notes):
        if reason["text"] not in seen:
            seen.add(reason["text"])
            reasons.append(reason)
    return Assessment(verdict, reasons)


PROFILE_KEY = "eligibility_profile"


def load_profile(session: Session) -> Profile:
    """The saved profile, or the default when none is saved (or it can't be read)."""
    row = session.get(Setting, PROFILE_KEY)
    if row is None:
        return DEFAULT_PROFILE
    try:
        data = json.loads(row.value)
        values = {f.name: data[f.name] for f in fields(Profile)}
        return Profile(**{k: tuple(v) if isinstance(v, list) else v for k, v in values.items()})
    except (ValueError, KeyError, TypeError):
        return DEFAULT_PROFILE


def save_profile(session: Session, profile: Profile, now: datetime) -> None:
    row = session.get(Setting, PROFILE_KEY) or Setting(key=PROFILE_KEY, value="")
    row.value, row.updated_at = json.dumps(asdict(profile)), now
    session.add(row)
    session.commit()


def effective(job) -> str:
    """The verdict that counts: the user's override, else the automatic one, else Check."""
    return job.eligibility_override or job.eligibility or "check"


def refresh(session: Session, job: Job, profile: Profile) -> None:
    """Decide the job again from its current texts; the user's override is left alone."""
    result = assess(job, profile)
    job.eligibility, job.eligibility_reasons = result.verdict, json.dumps(result.reasons, ensure_ascii=False)
    job.eligibility_rules = RULES_VERSION
    session.add(job)


def refresh_job(session_factory, job_id: int) -> None:
    with session_factory() as s:
        job = s.get(Job, job_id)
        if job is not None:
            refresh(s, job, load_profile(s))
            s.commit()


def refresh_all(session: Session, profile: Profile, *, stale_only: bool = False) -> int:
    """Check every job (or only those checked by older rules); returns how many were checked."""
    stmt = select(Job)
    if stale_only:
        stmt = stmt.where(or_(Job.eligibility_rules.is_(None), Job.eligibility_rules < RULES_VERSION))
    jobs = session.exec(stmt).all()
    for job in jobs:
        refresh(session, job, profile)
    session.commit()
    return len(jobs)
