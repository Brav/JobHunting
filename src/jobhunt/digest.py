"""The daily digest: new jobs the user can take, follow-ups and contacts due, applications closed for them, goals.
Shown at /digest; pushed to the phone only through an ntfy address the user set."""

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from jobhunt.models import Contact, Job
from jobhunt.preferences import Preferences
from jobhunt.stats import Goal, application_row, load_applications, weekly_goals

LIMIT = 20


@dataclass
class Digest:
    since: datetime
    new_jobs: list[Job] = field(default_factory=list)
    new_total: int = 0
    follow_ups: list[Job] = field(default_factory=list)
    contacts_due: list[Contact] = field(default_factory=list)
    auto_closed: list[Job] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.new_total:
            parts.append(f"{self.new_total} new job{'s' if self.new_total != 1 else ''} you can take")
        if self.follow_ups:
            parts.append(f"{len(self.follow_ups)} follow-up{'s' if len(self.follow_ups) != 1 else ''} due")
        if self.contacts_due:
            parts.append(f"{len(self.contacts_due)} contact{'s' if len(self.contacts_due) != 1 else ''} to follow up")
        if self.auto_closed:
            parts.append(f"{len(self.auto_closed)} closed without a reply")
        return " · ".join(parts)


def build_digest(session: Session, prefs: Preferences, since: datetime, now: datetime, today: date,
                 tz: ZoneInfo) -> Digest:
    from jobhunt.filters import EFFECTIVE_ELIGIBILITY
    stmt = (select(Job).where(Job.status == "new", Job.first_seen_at >= since, Job.blocked_by.is_(None),
                              EFFECTIVE_ELIGIBILITY.in_(("eligible", "relocate")))
            .order_by(Job.best_score.desc(), Job.first_seen_at.desc()))
    new = list(session.exec(stmt).all())
    apps = load_applications(session)
    return Digest(
        since=since, new_jobs=new[:LIMIT], new_total=len(new),
        follow_ups=[a.job for a in apps if application_row(a, today, prefs).follow_up],
        contacts_due=list(session.exec(select(Contact).where(Contact.follow_up_on.is_not(None),
                                                             Contact.follow_up_on <= today)).all()),
        auto_closed=[a.job for a in apps if a.job.auto_closed_on and a.job.stage == "no_response"
                     and a.job.auto_closed_on >= since.date()],
        goals=weekly_goals(session, apps, prefs, today, tz))


def push_digest(prefs: Preferences, digest: Digest, base_url: str, post) -> bool:
    """Send the summary to the user's ntfy address; nothing when none is set or there's nothing to say."""
    text = digest.summary()
    if not prefs.ntfy_url or not text:
        return False
    post(prefs.ntfy_url, data=text.encode(), timeout=15,
         headers={"Title": "Job tracker", "Click": f"{base_url.rstrip('/')}/digest", "Tags": "briefcase"})
    return True
