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
    run_every_hours: int = 4
    cv_path: Path = Path("/app/cv/original.pdf")
    deepl_auth_key: str = ""
    public_url: str = "http://127.0.0.1:8765"  # where the phone push links to


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    every = int(env.get("JOBHUNT_RUN_EVERY_HOURS", "4"))
    if every <= 0 or 24 % every:
        raise ValueError(f"JOBHUNT_RUN_EVERY_HOURS must divide 24, got {every}")
    return Settings(
        tz=ZoneInfo(env.get("TZ", "UTC")),
        run_at=datetime.strptime(env.get("JOBHUNT_RUN_AT", "10:00"), "%H:%M").time(),
        db_path=Path(env.get("JOBHUNT_DB_PATH", "/app/data/jobhunt.db")),
        results_per_listing=int(env.get("JOBHUNT_RESULTS_PER_LISTING", "100")),
        run_every_hours=every,
        cv_path=Path(env.get("JOBHUNT_CV_PATH", "/app/cv/original.pdf")),
        deepl_auth_key=env.get("DEEPL_AUTH_KEY", "").strip(),
        public_url=env.get("JOBHUNT_PUBLIC_URL", "http://127.0.0.1:8765").strip(),
    )
