"""A pasted email from an employer, read for what it says about an application: received, rejected, interview, offer."""

import re

from sqlmodel import Session, col, select

from jobhunt.applications import INTERVIEW_STAGES
from jobhunt.blocks import company_key
from jobhunt.models import Job

# Checked in this order: a rejection often thanks you for applying, and an invitation often does too. A bare
# "unfortunately" comes last: receipts say "unfortunately we can't reply to everyone".
_KINDS = [
    ("rejected", [r"not (?:be )?(?:moving|move|proceeding|proceed|progressing) forward",
                  r"(?:won't|will not|cannot|can't) (?:be )?(?:progress|proceed|mov|continu)\w*(?: forward)?",
                  r"regret to inform you", r"decided (?:not to|to (?:move|proceed|go) forward with other)",
                  r"(?:pursue|proceed with|move forward with|go with) other candidates",
                  r"(?:have )?not (?:been )?selected", r"(?:position|role) has (?:now )?been filled",
                  r"no longer (?:being )?considered", r"absage", r"nicht (?:weiter )?berücksichtig\w*",
                  r"für (?:einen anderen|eine andere) (?:kandidat|bewerber)\w*"]),
    ("offer", [r"(?:pleased|happy|delighted|excited) to (?:offer|extend)(?: you)?", r"(?:would|'d) like to offer you",
               r"(?:job|employment|formal|written) offer", r"offer letter", r"vertragsangebot"]),
    ("interview", [r"invit\w* you (?:to|for) (?:an? |the |our )?(?:\w+ )?(?:interview|call|chat|conversation|meeting)",
                   r"schedule (?:an? |the |a quick )?(?:\w+ )?(?:interview|call|chat|meeting|time)",
                   r"(?:book|pick|choose) a (?:time|slot)", r"calendly\.com",
                   r"(?:hr|phone|video|technical|tech|final|first|second|onsite|on-site) (?:interview|screen|round)",
                   r"your availability", r"vorstellungsgespräch", r"kennenlerngespräch", r"einladung zu\w*"]),
    ("received", [r"thank(?:s| you) for (?:applying|your application|your interest)",
                  r"(?:received|receipt of) your application", r"application (?:has been |was )?(?:received|submitted)",
                  r"vielen dank für (?:ihre|deine) bewerbung", r"eingangsbestätigung",
                  r"bewerbung (?:ist )?(?:eingegangen|erhalten)"]),
    ("rejected", [r"unfortunately", r"leider"]),
]
_PATTERNS = [(kind, [re.compile(p, re.I) for p in patterns]) for kind, patterns in _KINDS]
_LEGAL = {"gmbh", "ag", "kg", "ug", "se", "ltd", "limited", "inc", "llc", "plc", "corp", "corporation", "co",
          "company", "bv", "nv", "doo", "sa", "srl", "sro", "spa", "oy", "ab", "as", "group", "holding"}
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_GENERIC_LABELS = {"mail", "email", "jobs", "careers", "hr", "talent", "recruiting", "notifications", "noreply",
                   "no-reply", "apply", "hire", "www", "eu", "us", "co", "com"}


def classify(text: str) -> tuple[str | None, str | None]:
    """(kind, the phrase that decided it), or (None, None) when the email says nothing recognisable."""
    for kind, patterns in _PATTERNS:
        for pattern in patterns:
            m = pattern.search(text)
            if m:
                return kind, m.group(0)
    return None, None


def proposed_stage(kind: str | None, current: str | None) -> str | None:
    """The step the email suggests adding, given the application's current stage; None = nothing to add."""
    if kind == "received":
        return None if current else "applied"
    if kind == "interview":
        if current in INTERVIEW_STAGES:
            return INTERVIEW_STAGES[min(INTERVIEW_STAGES.index(current) + 1, len(INTERVIEW_STAGES) - 1)]
        return "hr_interview"
    return kind if kind in ("rejected", "offer") else None


def _core(company: str) -> str:
    """The company's name without its legal form: "Acme GmbH" → "acme", "Initech d.o.o." → "initech"."""
    words = company_key(company).split()
    while len(words) > 1 and (words[-1] in _LEGAL or len(words[-1]) == 1):
        words.pop()
    return " ".join(words)


def candidates(session: Session, text: str) -> list[Job]:
    """Saved and applied jobs whose company the email names, or whose domain it comes from; applied first."""
    labels = {label.casefold() for domain in _EMAIL.findall(text) for label in domain.split(".")[:-1]} - _GENERIC_LABELS
    jobs = session.exec(select(Job).where(col(Job.status).in_(("applied", "interested")))
                        .order_by(Job.status, col(Job.status_changed_at).desc(), col(Job.id).desc())).all()
    found = []
    for job in jobs:
        core = _core(job.company)
        if len(core) < 3:
            continue
        named = re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, core.split())) + r"(?!\w)", text, re.I)
        if named or core.replace(" ", "") in labels:
            found.append(job)
    return found[:10]


def note_for(text: str) -> str:
    """The step's note: the email's subject, or its first line."""
    subject = re.search(r"^subject:\s*(.+)$", text, re.I | re.M)
    line = subject.group(1) if subject else next((ln for ln in text.splitlines() if ln.strip()), "")
    return "From email: " + " ".join(line.split())[:120]
