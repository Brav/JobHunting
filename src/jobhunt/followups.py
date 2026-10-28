"""When to follow up on an application, and closing ones nobody answered as "No response"."""

from datetime import date, datetime

from sqlmodel import Session

from jobhunt.models import ApplicationStep, Job


def last_activity(job: Job, app) -> date | None:
    """The latest step, follow-up or automatic closing: what "days without a reply" counts from."""
    dates = [d for d in (app.latest_on if app else None, job.followed_up_on, job.auto_closed_on) if d]
    return max(dates) if dates else None


def follow_up_due(job: Job, app, prefs, today: date) -> bool:
    """Still at Applied (no reply yet) and quiet for the follow-up interval since the last step or follow-up."""
    if job.status != "applied" or job.stage != "applied":
        return False
    last = last_activity(job, app)
    return last is not None and (today - last).days >= prefs.follow_up_days


def auto_close(session: Session, prefs, today: date, now: datetime) -> int:
    """Close applications still at Applied after the auto-close interval with a "No response" step (once: deleting
    that step undoes it, and the count starts again from the closing)."""
    from jobhunt.applications import refresh_stage
    from jobhunt.stats import load_applications
    closed = 0
    for app in load_applications(session):
        job = app.job
        last = last_activity(job, app)
        if job.stage != "applied" or last is None or (today - last).days < prefs.auto_close_days:
            continue
        session.add(ApplicationStep(job_id=job.id, stage="no_response", on_date=today, created_at=now,
                                    note=f"Closed automatically after {prefs.auto_close_days} days without a reply"))
        job.auto_closed_on = today
        session.flush()
        refresh_stage(session, job)
        closed += 1
    session.commit()
    return closed
