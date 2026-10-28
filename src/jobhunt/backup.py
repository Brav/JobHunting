"""Backups and exports of the database.

Backups are SQLite online copies in <data>/backups, the newest 14 kept. Restoring replaces the data, so it is a command
(python -m jobhunt.backup restore <file>), not a button.
"""

import csv
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

from jobhunt.models import ApplicationStep, Job

KEEP = 14
_NAME = "jobhunt-{:%Y-%m-%d-%H%M}.db"


def backups(dest_dir: Path) -> list[Path]:
    """Backups, newest first (the names sort by time)."""
    return sorted(dest_dir.glob("jobhunt-*.db"), reverse=True) if dest_dir.is_dir() else []


def _copy(db_path: Path, target: Path) -> None:
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()


def prune(dest_dir: Path, keep: int = KEEP) -> None:
    for old in backups(dest_dir)[keep:]:
        old.unlink()


def backup_now(db_path: Path, dest_dir: Path, now: datetime) -> Path:
    """Copy the database (safe while the app runs) and keep only the newest backups. `now` is local time."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / _NAME.format(now)
    _copy(db_path, target)
    prune(dest_dir)
    return target


def ensure_recent_backup(db_path: Path, dest_dir: Path, now: datetime) -> Path | None:
    """Back up when the newest backup is more than a day old (before startup changes the database)."""
    newest = backups(dest_dir)
    if newest and now - datetime.strptime(newest[0].stem, "jobhunt-%Y-%m-%d-%H%M") < timedelta(hours=24):
        return None
    return backup_now(db_path, dest_dir, now)


def jobs_csv(session: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    columns = ["id", "title", "title_en", "company", "location", "url", "status", "stage", "eligibility",
               "eligibility_override", "workplace", "language", "seniority", "posted_at", "first_seen_at"]
    writer.writerow(columns)
    for job in session.exec(select(Job).order_by(Job.id)):
        writer.writerow([getattr(job, c) if getattr(job, c) is not None else "" for c in columns])
    return out.getvalue()


def applications_csv(session: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["job_id", "company", "title", "status", "current_stage", "step", "on_date", "note"])
    rows = session.exec(select(ApplicationStep, Job).join(Job, Job.id == ApplicationStep.job_id)
                        .order_by(ApplicationStep.job_id, ApplicationStep.on_date, ApplicationStep.id))
    for step, job in rows:
        writer.writerow([job.id, job.company, job.title_en or job.title, job.status, job.stage or "", step.stage,
                         step.on_date.isoformat(), step.note])
    return out.getvalue()


def export_zip(db_path: Path, session: Session, now: datetime) -> bytes:
    """A zip with a fresh copy of the database and both CSV files."""
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        copy = Path(tmp) / "jobhunt.db"
        _copy(db_path, copy)
        z.write(copy, "jobhunt.db")
        z.writestr("jobs.csv", jobs_csv(session))
        z.writestr("applications.csv", applications_csv(session))
    return buffer.getvalue()


def _in_use(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(db_path, timeout=0)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.rollback()
        finally:
            conn.close()
        return False
    except sqlite3.OperationalError:
        return True


def restore(db_path: Path, backup_file: Path, *, now: datetime, force: bool = False) -> Path:
    """Replace the database with a backup; the current one is kept next to it. Returns where it was kept."""
    if not force and _in_use(db_path):
        raise RuntimeError("The database is in use. Stop the app first (docker compose stop), or pass --force.")
    aside = db_path.with_name(f"{db_path.name}.before-restore-{now:%Y%m%d-%H%M%S}")
    _copy(db_path, aside)
    incoming = db_path.with_name(db_path.name + ".restoring")
    shutil.copyfile(backup_file, incoming)
    for suffix in ("-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    os.replace(incoming, db_path)  # a new file, never the old one rewritten in place
    return aside


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "restore":
        print("usage: python -m jobhunt.backup restore <backup file> [--force]")
        return 2
    from jobhunt.config import load_settings
    settings = load_settings()
    aside = restore(settings.db_path, Path(argv[2]), now=datetime.now(), force="--force" in argv)
    print(f"Restored {argv[2]}; the previous database is at {aside}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
