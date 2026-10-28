"""Engine and session setup for the SQLite database."""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, event, update
from sqlmodel import Session, SQLModel, create_engine

from jobhunt.models import Run
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
        cursor.close()
        dbapi_conn.create_function("py_lower", 1, _py_lower, deterministic=True)

    return engine


def init_db(engine: Engine) -> None:
    """Create tables and mark runs left 'running' by a crash or restart as interrupted."""
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(
            update(Run)
            .where(Run.status == "running")
            .values(status="failed", error="interrupted", finished_at=utcnow())
        )
        session.commit()


def make_session_factory(engine: Engine) -> Callable[[], Session]:
    return lambda: Session(engine, expire_on_commit=False)
