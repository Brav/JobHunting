"""Application stages: dated steps after a job is marked Applied, and the job's current stage."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from jobhunt.models import ApplicationStep, Job
from jobhunt.timeutil import local_date

STAGES = ("applied", "hr_interview", "technical_interview", "final_interview", "offer",
          "rejected", "withdrawn", "no_response")
PIPELINE = STAGES[:5]
END_STAGES = STAGES[5:]
INTERVIEW_STAGES = ("hr_interview", "technical_interview", "final_interview")
REPLY_STAGES = frozenset(STAGES) - {"applied", "withdrawn", "no_response"}
STAGE_LABELS = {"applied": "Applied", "hr_interview": "HR interview", "technical_interview": "Technical interview",
                "final_interview": "Final interview", "offer": "Offer", "rejected": "Rejected",
                "withdrawn": "Withdrawn", "no_response": "No response"}
STALE_DAYS = 14
NOTE_MAX = 500
UNDO_WINDOW = timedelta(minutes=10)  # an automatic Applied step this new is removed when the job leaves Applied


class StepError(ValueError):
    """A step that can't be added; the message is shown to the user."""


def _order(step: ApplicationStep):
    return step.on_date, step.created_at, step.id or 0


def steps_for(session: Session, job_id: int) -> list[ApplicationStep]:
    """The job's steps, newest first."""
    steps = session.exec(select(ApplicationStep).where(ApplicationStep.job_id == job_id)).all()
    return sorted(steps, key=_order, reverse=True)


def current_stage(steps: Iterable[ApplicationStep]) -> str | None:
    # Applied is where every application starts: any other step, whatever its date, is further along.
    steps = list(steps)
    later = [s for s in steps if s.stage != "applied"]
    if later:
        return max(later, key=_order).stage
    return "applied" if steps else None


def refresh_stage(session: Session, job: Job) -> None:
    job.stage = current_stage(steps_for(session, job.id)) if job.status == "applied" else None
    session.add(job)


def add_step(session: Session, job_id: int, stage: str, on_date: date, note: str, today: date,
             now: datetime) -> ApplicationStep:
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(f"no job with id {job_id}")
    note = note.strip()
    if job.status != "applied":
        raise StepError("Mark the job Applied first")
    if stage not in STAGES:
        raise StepError("Unknown stage")
    if on_date > today:
        raise StepError("The date can't be in the future")
    if len(note) > NOTE_MAX:
        raise StepError(f"Keep the note to {NOTE_MAX} characters")
    step = ApplicationStep(job_id=job_id, stage=stage, on_date=on_date, note=note, created_at=now)
    session.add(step)
    session.flush()
    refresh_stage(session, job)
    session.commit()
    return step


def delete_step(session: Session, job_id: int, step_id: int) -> None:
    step = session.get(ApplicationStep, step_id)
    if step is None or step.job_id != job_id:
        raise LookupError(f"no step {step_id} for job {job_id}")
    session.delete(step)
    session.flush()
    refresh_stage(session, session.get(Job, job_id))
    session.commit()


def next_stage(stage: str | None) -> str:
    """The stage a new step defaults to: the next one in the pipeline."""
    if stage is None:
        return "applied"
    if stage in PIPELINE[:-1]:
        return PIPELINE[PIPELINE.index(stage) + 1]
    return stage


def stage_kind(stage: str) -> str:
    if stage == "offer":
        return "stage-offer"
    return "stage-closed" if stage in END_STAGES else "stage-open"


def on_status_change(session: Session, job: Job, previous: str, today: date, now: datetime) -> None:
    """Keep the steps in line with the status: applying starts a history, leaving keeps it (unless it's empty)."""
    steps = steps_for(session, job.id)
    if job.status == "applied" and not steps:
        session.add(ApplicationStep(job_id=job.id, stage="applied", on_date=today, created_at=now))
        session.flush()
    elif previous == "applied" and job.status != "applied":
        # A lone Applied step added moments ago was a slip: an Apply + Undo leaves no trace. An older one is a
        # real application date and stays, so leaving Applied by mistake and undoing it later loses nothing.
        if (len(steps) == 1 and steps[0].stage == "applied" and not steps[0].note
                and now - steps[0].created_at <= UNDO_WINDOW):
            session.delete(steps[0])
            session.flush()
    refresh_stage(session, job)


def backfill_applied_steps(session: Session, tz: ZoneInfo) -> None:
    """Jobs marked Applied before steps existed get an Applied step dated when they were marked."""
    with_steps = select(ApplicationStep.job_id).where(ApplicationStep.job_id == Job.id).exists()
    for job in session.exec(select(Job).where(Job.status == "applied", ~with_steps)).all():
        marked = job.status_changed_at or job.first_seen_at
        session.add(ApplicationStep(job_id=job.id, stage="applied", on_date=local_date(marked, tz), created_at=marked))
        session.flush()
        refresh_stage(session, job)
    session.commit()
