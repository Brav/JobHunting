"""Settings read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    tz: ZoneInfo
    run_at: time
    db_path: Path
    results_per_listing: int


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    return Settings(
        tz=ZoneInfo(env.get("TZ", "Europe/Belgrade")),
        run_at=datetime.strptime(env.get("JOBHUNT_RUN_AT", "10:00"), "%H:%M").time(),
        db_path=Path(env.get("JOBHUNT_DB_PATH", "/app/data/jobhunt.db")),
        results_per_listing=int(env.get("JOBHUNT_RESULTS_PER_LISTING", "100")),
    )
