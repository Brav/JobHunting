# Job tracker

Searches LinkedIn every day at 10:00 for each of your saved searches ("listings") and shows the new jobs in a
local dashboard at http://localhost:8765, where you can mark jobs Interested, Applied or Hidden.

Design: `docs/superpowers/specs/2026-10-28-linkedin-job-tracker-design.md`

## Prerequisites

- [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker with Compose v2 (`docker compose version` should print v2.x).
  On Ubuntu: `sudo apt install docker-compose-v2`.

## Run it (Docker)

```bash
mkdir -p data
docker compose up -d --build
```

Open http://localhost:8765, add a listing on the **Listings** page, and press **Run now** to try it straight away.
The container restarts on its own after reboots and crashes. The database lives in `data/jobhunt.db`.

Logs: `docker compose logs -f jobhunt`

After changing the code: `docker compose up -d --build`

## Develop

```bash
uv run pytest
JOBHUNT_DB_PATH=./data/jobhunt.db uv run uvicorn --factory jobhunt.main:build_app --reload
```

## Check the LinkedIn scraper

```bash
uv run python -m jobhunt.smoke "python developer" "Belgrade, Serbia" 25
```

Prints up to 25 jobs from the last 7 days. If it reports `rate limited by LinkedIn`, wait a few hours.

## When LinkedIn changes its pages

Runs will show as failed in the dashboard. Update the scraper library and rebuild:

```bash
uv lock --upgrade-package python-jobspy
docker compose up -d --build
```

## Configuration

Environment variables (set them in `docker-compose.yml`):

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `Europe/Belgrade` | Time zone for the schedule and the dashboard |
| `JOBHUNT_RUN_AT` | `10:00` | Daily run time (HH:MM, local) |
| `JOBHUNT_DB_PATH` | `/app/data/jobhunt.db` | SQLite file |
| `JOBHUNT_RESULTS_PER_LISTING` | `100` | Max jobs fetched per listing per run |
