"""Workplace type (remote / hybrid / on-site) from clear words in a job's texts, in several languages."""

import json
import re

WORKPLACES = ("remote", "hybrid", "on-site")
WORKPLACE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site"}


def _words(*phrases: str) -> re.Pattern:
    # Whole words only: "remoteness" or "onsiteX" must not count.
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in phrases) + r")(?!\w)", re.IGNORECASE)


_RULES = (  # checked in this order within each text
    ("hybrid", _words("hybrid", "hybrides arbeiten", "ibrido", "híbrido")),
    ("remote", _words("remote", "fully remote", "100% remote", "work from home", "wfh", "homeoffice", "home office",
                      "telearbeit", "remoto", "télétravail", "teletrabajo", "zdalnie", "rad od kuće")),
    ("on-site", _words("on-site", "onsite", "in office", "vor ort", "presencial", "in presenza", "sur site")),
)


def workplace_from(*texts: str | None, is_remote: bool | None = None) -> str | None:
    """The first text (in the order given) with a clear workplace word decides; else JobSpy's remote flag."""
    for text in texts:
        if not text:
            continue
        for workplace, pattern in _RULES:
            if pattern.search(text):
                return workplace
    return "remote" if is_remote else None


def details_value(details, label: str) -> str | None:
    """The value for `label` in LinkedIn's details box ([label, value] pairs), ignoring case."""
    for item_label, value in details or []:
        if item_label.casefold() == label.casefold():
            return value
    return None


def details_text(details_json: str | None) -> str:
    """The details box's values as one string, for workplace detection."""
    return ", ".join(value for _, value in json.loads(details_json)) if details_json else ""
