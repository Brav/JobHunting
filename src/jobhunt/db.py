"""Engine and session setup for the SQLite database."""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, event, update
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunt.dedup import match_key
from jobhunt.models import Job, Run
from jobhunt.timeutil import utcnow


def _py_lower(value):
    # SQLite's lower() only folds ASCII; this handles Cyrillic and accented letters.
    return value.lower() if isinstance(value, str) else value


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        dbapi_conn.create_function("py_lower", 1, _py_lower, deterministic=True)

    return engine


def _add_missing_columns(engine: Engine) -> None:
    # No migration tool: columns added to a model after a database was created are added here (nullable only).
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
            for column in table.columns:
                if column.name not in existing and column.nullable:
                    ddl_type = column.type.compile(dialect=engine.dialect)
                    conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type}")


def init_db(engine: Engine) -> None:
    """Create tables and mark runs left 'running' by a crash or restart as interrupted."""
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_jobs_match_key ON jobs (match_key)")
    with Session(engine) as session:
        for job in session.exec(select(Job).where(Job.match_key.is_(None))):
            job.match_key = match_key(job.title, job.company)
            session.add(job)
        session.commit()
    with Session(engine) as session:
        session.exec(
            update(Run)
            .where(Run.status == "running")
            .values(status="failed", error="interrupted", finished_at=utcnow())
        )
        session.commit()


def make_session_factory(engine: Engine) -> Callable[[], Session]:
    return lambda: Session(engine, expire_on_commit=False)
