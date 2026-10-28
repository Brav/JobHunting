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
    # You being paid in it — not a payments product that handles it ("payment gateway for Bitcoin merchants").
    ("Pays in crypto or gift cards", [re.compile(p, _I) for p in (
        r"\b(?:paid|payments?|salary|wages?|pay|compensation)\s+(?:is\s+|will\s+be\s+)?(?:\w+\s+){0,2}?"
        r"(?:in|via|with|through|using)\s+(?:crypto(?:currency)?|bitcoin|USDT|gift\s*cards?)\b",
        r"\b(?:crypto(?:currency)?|bitcoin|USDT|gift\s*cards?)\s+(?:as\s+)?(?:salary|wages|compensation)\b",
    )]),
    # Being told to get in touch there — not a product built on it ("integrations with the WhatsApp Business API").
    ("Contact on WhatsApp, Telegram or Signal", [re.compile(p, _I) for p in (
        r"\b(?:(?:contact|message|text|reach(?:\s+out\s+to)?|write\s+to)\s+(?:us|me|our\s+[\w-]+|the\s+(?:recruiter|team|hr)|hr)|"
        r"apply|send\s+(?:us\s+)?(?:your\s+)?(?:cv|resume|application|portfolio|details))\b(?:\s+[\w-]+){0,3}?"
        r"\s+(?:on|via|through|over|using|at)\s+(?:WhatsApp|Telegram|Signal)\b(?!-)",
        r"\b(?:WhatsApp|Telegram)\b\s*(?::|at|on)?\s*\+?\d[\d\s-]{6,}",
    )]),
    ("Personal email address to apply", [re.compile(
        r"\b(?:apply|send|email|cv|resume|contact|write)\b[^.\n]{0,60}?[\w.+-]+@(?:gmail|yahoo|outlook|hotmail|aol|"
        r"proton(?:mail)?)\.\w+", _I)]),
    ("Paid per task", [re.compile(p, _I) for p in (
        r"\b(?:paid|pay|earn)\s+per\s+task\b", r"\brate\s+(?:the\s+)?apps\b",
        r"\b(?:task|order)[- ]based\s+(?:pay|commission|earnings)\b", r"\bcomplete\s+tasks\s+(?:to|and)\s+earn\b",
    )]),
    ("Work from your phone", [re.compile(r"\bwork\s+(?:from|on)\s+your\s+(?:phone|smartphone|mobile)\b"
                                         r"(?!\s*(?:app|application|banking|game))", _I)]),
]
NO_EXPERIENCE = re.compile(r"\bno\s+experience\s+(?:needed|required|necessary)\s*[!.]", _I)
HIGH_PAY = re.compile(r"\bearn\s+up\s+to\b|[$€£]\s?\d{3,}\s*(?:per|/|a)\s*(?:day|week)\b", _I)
NEGATION = re.compile(r"\b(?:no|not|never|without|free\s+of)\b", _I)
# Not asked of you: a perk ("Registration Fees Paid", "fees are reimbursed", "fee waived") or a thing being built
# ("the application processing fee calculator").
PAID_FOR_YOU = re.compile(r"\s*(?:(?:are|is|will\s+be|get)\s+)?(?:paid|covered|reimbursed|funded|included|waived|refunded)\b|"
                          r"\s+(?:calculator|calculation|engine|logic|module|service|system|api|model|structure|"
                          r"management|tracking)s?\b", _I)
PAYS_FOR_YOU = re.compile(r"\b(?:we|company|employer)\s+(?:will\s+)?(?:pay|pays|cover|covers|reimburse|reimburses|fund|funds)"
                          r"\s+(?:for\s+)?(?:your|all|any)?\s*$", _I)


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
            before = text[max(0, m.start() - 25):m.start()] if m else ""
            if m and not (NEGATION.search(before) or PAYS_FOR_YOU.search(before)  # "no registration fee"
                          or PAID_FOR_YOU.match(text, m.end())):
                found.append({"text": label, "quote": _sentence(text, *m.span())})
                break
    no_experience = NO_EXPERIENCE.search(text)
    if no_experience and HIGH_PAY.search(text):
        found.append({"text": "No experience needed, high pay", "quote": _sentence(text, *no_experience.span())})
    return found
