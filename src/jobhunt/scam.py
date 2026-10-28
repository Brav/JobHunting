"""Warning signs of a fake job (after the FTC's and LinkedIn's lists): fees, crypto pay, messenger-only contact,
personal email addresses, too-good pay for no experience, pay per task. Shown as a warning, never used to hide."""

import re

from jobhunt.models import Job

_I = re.IGNORECASE
RULES = [
    ("Asks you to pay a fee", [re.compile(p, _I) for p in (
        r"\b(?:registration|training|equipment|application|processing|onboarding)\s+fees?\b",
        r"\bstarter\s+kit\b",
        r"\bpay\s+(?:a\s+fee\s+)?for\s+(?:your\s+)?(?:own\s+)?(?:training|equipment|registration|onboarding)\b",
    )]),
    ("Pays in crypto or gift cards", [re.compile(p, _I) for p in (
        r"\b(?:paid|payment|salary|wages?|pay)\b[^.\n]{0,40}\b(?:crypto(?:currency)?|bitcoin|USDT|gift\s*cards?)\b",
        r"\b(?:crypto(?:currency)?|bitcoin|USDT|gift\s*cards?)\b[^.\n]{0,20}\b(?:payment|salary|wages)\b",
    )]),
    ("Contact on WhatsApp, Telegram or Signal", [re.compile(p, _I) for p in (
        r"\b(?:contact|message|text|reach|write|apply|send|chat)\b[^.\n]{0,40}\b(?:WhatsApp|Telegram|Signal)\b",
        r"\b(?:WhatsApp|Telegram)\b\s*(?::|at|on)?\s*\+?\d[\d\s-]{6,}",
    )]),
    ("Personal email address to apply", [re.compile(
        r"\b(?:apply|send|email|cv|resume|contact|write)\b[^.\n]{0,60}?[\w.+-]+@(?:gmail|yahoo|outlook|hotmail|aol|"
        r"proton(?:mail)?)\.\w+", _I)]),
    ("Paid per task", [re.compile(p, _I) for p in (
        r"\b(?:paid|pay|earn)\s+per\s+task\b", r"\brate\s+(?:the\s+)?apps\b",
        r"\b(?:task|order)[- ]based\s+(?:pay|commission|earnings)\b", r"\bcomplete\s+tasks\s+(?:to|and)\s+earn\b",
    )]),
    ("Work from your phone", [re.compile(r"\bwork\s+(?:from|on)\s+your\s+(?:phone|smartphone|mobile)\b", _I)]),
]
NO_EXPERIENCE = re.compile(r"\bno\s+experience\s+(?:needed|required|necessary)\s*[!.]", _I)
HIGH_PAY = re.compile(r"\bearn\s+up\s+to\b|[$€£]\s?\d{3,}\s*(?:per|/|a)\s*(?:day|week)\b", _I)
NEGATION = re.compile(r"\b(?:no|not|never|without|free\s+of)\b", _I)


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    rights = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    return " ".join(text[left:min(rights) + 1 if rights else len(text)].split())


def scam_flags(job: Job) -> list[dict]:
    """Each warning sign found, with the sentence it was found in."""
    text = re.sub(r"[*`]+", "", "\n".join(t for t in (job.title, job.description_en or job.description) if t))
    found = []
    for label, patterns in RULES:
        for pattern in patterns:
            m = pattern.search(text)
            if m and not NEGATION.search(text[max(0, m.start() - 25):m.start()]):  # "no registration fee"
                found.append({"text": label, "quote": _sentence(text, *m.span())})
                break
    no_experience = NO_EXPERIENCE.search(text)
    if no_experience and HIGH_PAY.search(text):
        found.append({"text": "No experience needed, high pay", "quote": _sentence(text, *no_experience.span())})
    return found
