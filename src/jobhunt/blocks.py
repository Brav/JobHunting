"""The user's own hiding rules: blocked companies and negative keywords, plus why they hid a job."""

import re
from datetime import datetime

from sqlmodel import Session, select

from jobhunt.models import BlockedCompany, Job, NegativeRule

SCOPES = {"title": "Title", "anywhere": "Title or description"}
HIDE_REASONS = {"not_eligible": "Not eligible", "stack": "Stack", "seniority": "Seniority", "salary": "Salary",
                "company": "Company", "language": "Language", "other": "Other"}


def company_key(name: str) -> str:
    """The same company however it's written: "ACME  GmbH" and "acme gmbh"."""
    return " ".join(re.sub(r"[^\w\s&]", " ", name.casefold()).split())


def _has(phrase: str, text: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE))


def blocked_reason(job: Job, blocked: set[str], rules: list[NegativeRule]) -> str | None:
    """Why the user's rules hide this job, or None."""
    if job.company and company_key(job.company) in blocked:
        return "Company blocked"
    title = " ".join(t for t in (job.title, job.title_en) if t)
    text = " ".join(t for t in (title, job.description_en or job.description) if t)
    for rule in rules:
        if not rule.active:
            continue
        if rule.scope == "title" and _has(rule.phrase, title):
            return f'Keyword "{rule.phrase}" in title'
        if rule.scope == "anywhere" and _has(rule.phrase, text):
            return f'Keyword "{rule.phrase}"'
    return None


def blocked_keys(session: Session) -> set[str]:
    return set(session.exec(select(BlockedCompany.key)).all())


def active_rules(session: Session) -> list[NegativeRule]:
    return list(session.exec(select(NegativeRule).where(NegativeRule.active == True)).all())  # noqa: E712


def block_company(session: Session, name: str, now: datetime) -> None:
    key = company_key(name)
    if key and session.get(BlockedCompany, key) is None:
        session.add(BlockedCompany(key=key, name=" ".join(name.split()), created_at=now))
        session.commit()


def unblock_company(session: Session, name: str) -> None:
    row = session.get(BlockedCompany, company_key(name))
    if row is not None:
        session.delete(row)
        session.commit()


def add_rule(session: Session, phrase: str, scope: str, now: datetime) -> None:
    phrase = " ".join(phrase.split())
    if phrase and scope in SCOPES:
        session.add(NegativeRule(phrase=phrase, scope=scope, created_at=now))
        session.commit()


def remove_rule(session: Session, rule_id: int) -> None:
    row = session.get(NegativeRule, rule_id)
    if row is not None:
        session.delete(row)
        session.commit()
