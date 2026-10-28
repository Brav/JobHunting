# Job tracker

Searches LinkedIn every 4 hours for each of your saved searches ("listings") and shows the new jobs in a local
dashboard at http://localhost:8765. Each job appears once; you can mark jobs Interested, Applied or Hidden, and get a
ready-made prompt for tailoring your CV to any job in Claude.ai.

Design: `docs/superpowers/specs/2026-10-28-linkedin-job-tracker-design.md`

## Prerequisites

- [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker with Compose v2 (`docker compose version` should print v2.x).

## Run it (Docker)

```bash
cp .env.example .env    # then set JOBHUNT_CV_FILE to your CV's path
mkdir -p data
docker compose up -d --build
```

Open http://localhost:8765, add a listing on the **Listings** page, and press **Run now** to try it straight away.
The container restarts on its own after reboots and crashes. The database lives in `data/jobhunt.db`.

Logs: `docker compose logs -f jobhunt`

After changing the code: `docker compose up -d --build`

## How searches run

- Every 4 hours at 02:00, 06:00, 10:00, 14:00, 18:00 and 22:00 (Belgrade time), all active listings run one after
  another. If a run time is missed (restart, sleep), the app catches up within 15 minutes.
- A job is the same job when its title and company match, whatever listing or LinkedIn ID it came from. It shows up
  once, under the listing that found it first, on the day it was first found.
- Editing a listing's keywords, location or radius makes its next run look back 7 days again.

## Tailor your CV

Every job has a **CV prompt** button. It opens a page with a prompt containing tailoring rules, the job's description
(fetched from LinkedIn the first time) and the text of your CV. Press **Copy prompt**, open Claude.ai and paste it.

Your original CV is never modified: it is mounted into the container read-only, and the app only reads it.
Set its location in `.env`:

```
JOBHUNT_CV_FILE=/home/you/Documents/CV.pdf
```

## Develop

```bash
uv run pytest
JOBHUNT_DB_PATH=./data/dev.db JOBHUNT_CV_PATH="/path/to/CV.pdf" \
  uv run uvicorn --factory jobhunt.main:build_app --reload --port 8766
```

Use a separate database (as above) while the container is running: two schedulers on one database would run every
search twice.

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

Environment variables (set them in `docker-compose.yml`, except `JOBHUNT_CV_FILE` which goes in `.env`):

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `Europe/Belgrade` | Time zone for the schedule and the dashboard |
| `JOBHUNT_RUN_AT` | `10:00` | Anchor time for the runs (HH:MM, local) |
| `JOBHUNT_RUN_EVERY_HOURS` | `4` | Hours between runs; must divide 24 |
| `JOBHUNT_DB_PATH` | `/app/data/jobhunt.db` | SQLite file |
| `JOBHUNT_RESULTS_PER_LISTING` | `100` | Max jobs fetched per listing per run |
| `JOBHUNT_CV_PATH` | `/app/cv/original.pdf` | The CV inside the container |
| `JOBHUNT_CV_FILE` | — | Your CV on this computer (in `.env`; mounted read-only) |
