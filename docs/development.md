# Developer notes

For people running the tracker from the source code or changing it. The step-by-step guide for everyone else is the
[README](../README.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker with Compose v2 (`docker compose version` should print v2.x).

## Run from the source (with a CV folder)

The root `docker-compose.yml` builds the image from this checkout, keeps the database in `./data` and mounts a
folder holding your CV read-only, so the app can never change the CV:

```bash
cp .env.example .env    # then set JOBHUNT_CV_DIR and JOBHUNT_CV_NAME to your CV
mkdir -p data
docker compose up -d --build
```

- On Linux, if your user id isn't 1000 (`id -u`), set `JOBHUNT_UID` and `JOBHUNT_GID` in `.env` so `./data` stays
  yours.
- A CV from `JOBHUNT_CV_DIR` wins over one uploaded in Settings. Replacing the file (same name) takes effect at once.
- Logs: `docker compose logs -f jobhunt`.
- After changing the code: `docker compose up -d --build`.

## Develop

```bash
uv run pytest
JOBHUNT_DB_PATH=./data/dev/jobhunt.db JOBHUNT_CV_PATH="/path/to/CV.pdf" \
  uv run uvicorn --factory jobhunt.main:build_app --reload --port 8766
```

Use a separate database in its own folder (as above) while the container is running: two schedulers on one database
would run every search twice, and backups go to a `backups` folder beside the database.

Tests never use the network. A new database starts on the setup page; the test fixtures save a profile first unless a
test asks for a fresh install (`app_factory(setup_done=False)`, `fresh_session_factory`).

## How searches run

- Every 4 hours, at 02:00, 06:00, 10:00, 14:00, 18:00 and 22:00 in the user's time zone (the one in Settings), all
  active listings run one after another. A missed run time (restart, sleep) is caught up within 15 minutes.
- A job is the same job when its title and company match, whatever listing or LinkedIn ID it came from.
- Editing a listing's keywords, location or radius makes its next run look back 7 days again.
- Job pages are downloaded in the background: every 5 minutes, up to 15 jobs, about 15 seconds apart, paused while a
  search runs. If LinkedIn blocks a download, the app waits 30 minutes.

## Backups and restore

The app copies its database to `backups/` beside it every night at 03:30, and when it starts after a day without
one, keeping the newest 14. **Settings → Backups and export** makes one now and downloads everything as a zip or CSV.

To restore a backup with the root compose file (the current database is kept next to it as
`jobhunt.db.before-restore-…`):

```bash
docker compose stop
docker compose run --rm jobhunt /app/.venv/bin/python -m jobhunt.backup restore data/backups/<file>
docker compose start
```

With the install compose file (a Docker volume), the same works from the folder holding that file:

```bash
docker compose stop
docker compose run --rm jobhunt /app/.venv/bin/python -m jobhunt.backup restore /app/data/backups/<file>
docker compose start
```

The restore refuses while the app is running.

## Use with Claude Desktop (MCP)

The tracker is also an MCP server, so Claude can search your jobs and update applications in a chat ("which saved
jobs fit best?", "I had the HR call with Acme today"). Its tools: `search_jobs`, `get_job`, `list_applications`,
`stats_summary`, `add_application_step` and `set_job_status`. It uses the same database as the app, on your
machine; nothing is uploaded except what Claude reads in the conversation.

Add this to Claude Desktop's config (`~/.config/Claude/claude_desktop_config.json` on Linux,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "job-tracker": {
      "command": "/home/you/.local/bin/uv",
      "args": ["run", "--directory", "/home/you/job_hunting", "python", "-m", "jobhunt.mcp_server"],
      "env": {"JOBHUNT_DB_PATH": "/home/you/job_hunting/data/jobhunt.db"}
    }
  }
}
```

The server (`jobhunt.mcp_server`) uses the time zone saved in Settings. Start the web app once after each update
first: it updates the database, and the MCP server refuses an older one.

## Check the LinkedIn scraper

```bash
uv run python -m jobhunt.smoke "python developer" "Berlin, Germany" 25
```

Prints up to 25 jobs from the last 7 days. If it reports `rate limited by LinkedIn`, wait a few hours.

When LinkedIn changes its pages, runs show as failed in the dashboard. Update the scraper library and rebuild:

```bash
uv lock --upgrade-package python-jobspy
docker compose up -d --build
```

## Configuration

Settings a user changes live in the app: the profile and time zone, the CV, the DeepL key, skills, goals, job sites
and the phone push. These environment variables are for advanced use. Set them in `docker-compose.yml`, except
`JOBHUNT_CV_DIR`, `JOBHUNT_CV_NAME`, `JOBHUNT_UID`, `JOBHUNT_GID` and `DEEPL_AUTH_KEY`, which go in `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `UTC` | The time zone until the user saves one in setup or Settings; after that, Settings decides |
| `JOBHUNT_RUN_AT` | `10:00` | Anchor time for the runs (HH:MM, in the user's time zone) |
| `JOBHUNT_RUN_EVERY_HOURS` | `4` | Hours between runs; must divide 24 |
| `JOBHUNT_DB_PATH` | `/app/data/jobhunt.db` | SQLite file; the uploaded CV, the DeepL key file and backups sit beside it |
| `JOBHUNT_RESULTS_PER_LISTING` | `100` | Max jobs fetched per listing per run |
| `JOBHUNT_PUBLIC_URL` | `http://127.0.0.1:8765` | Where links in the phone push point |
| `JOBHUNT_CV_PATH` | not set | A CV file outside the app; wins over the uploaded one (compose sets it from the two below) |
| `JOBHUNT_CV_DIR` | — | Folder holding your CV on this computer (in `.env`; mounted read-only) |
| `JOBHUNT_CV_NAME` | — | The CV's file name in that folder (in `.env`) |
| `JOBHUNT_UID`, `JOBHUNT_GID` | `1000` | The user the root compose runs as, so `./data` stays yours (in `.env`) |
| `DEEPL_AUTH_KEY` | empty | DeepL API key (in `.env`); wins over the key saved in Settings |

## CI, the published image and releases

`.github/workflows/ci.yml` runs the tests on every push and pull request. After the tests pass on `main`, it builds
the image for `linux/amd64` and `linux/arm64` and publishes `ghcr.io/brav/jobhunting:latest`; a tag `v*` (for example
`v0.2.0`) publishes `ghcr.io/brav/jobhunting:0.2.0`. It only uses the workflow's own `GITHUB_TOKEN`.

The first time, GitHub publishes the package as private. Make it public once, so people can download it without
signing in: on GitHub, open your profile → **Packages** → **jobhunting** → **Package settings** →
**Change visibility** → **Public**.

To release a version: set `version` in `pyproject.toml`, commit, then

```bash
git tag v0.2.0
git push origin v0.2.0
```

## Demo data and screenshots

The README's screenshots come from a demo database with made-up jobs, never real data:

```bash
uv run python scripts/demo_data.py /tmp/demo/demo.db
```

Serve it with `JOBHUNT_DB_PATH=/tmp/demo/demo.db` (no scheduler needed), then take each screenshot with headless
Chrome, its time zone set so the setup page doesn't show your own:

```bash
TZ=Europe/Berlin google-chrome --headless=new --hide-scrollbars --user-data-dir=/tmp/demo/chrome \
  --window-size=1280,860 --screenshot=docs/images/report.png http://127.0.0.1:8768/
```
