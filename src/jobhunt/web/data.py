"""Backups and exports: back up now, download a backup, export everything as a zip or CSV."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from jobhunt.backup import applications_csv, backup_now, backups, export_zip, jobs_csv
from jobhunt.web.common import render, session

router = APIRouter()


def _dir(request: Request):
    return request.app.state.settings.db_path.parent / "backups"


def _local_now(request: Request) -> datetime:
    return datetime.now(request.app.state.settings.tz).replace(tzinfo=None)


def _attachment(content, media_type: str, filename: str) -> Response:
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/data", response_class=HTMLResponse)
def data_page(request: Request, made: str = ""):
    files = [(b.name, b.stat().st_size) for b in backups(_dir(request))]
    return render(request, "data.html", active="settings", files=files, made=made)


@router.post("/data/backup")
def make_backup(request: Request):
    path = backup_now(request.app.state.settings.db_path, _dir(request), _local_now(request))
    return RedirectResponse(f"/data?made={path.name}", status_code=303)


@router.get("/data/backups/{name}")
def download_backup(request: Request, name: str):
    match = next((b for b in backups(_dir(request)) if b.name == name), None)  # only listed files, never a path
    if match is None:
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(match, media_type="application/vnd.sqlite3", filename=match.name)


@router.get("/data/export.zip")
def export(request: Request):
    now = _local_now(request)
    with session(request) as s:
        content = export_zip(request.app.state.settings.db_path, s, now)
    return _attachment(content, "application/zip", f"jobhunt-export-{now:%Y-%m-%d}.zip")


@router.get("/data/jobs.csv")
def export_jobs(request: Request):
    with session(request) as s:
        return _attachment(jobs_csv(s), "text/csv; charset=utf-8", "jobs.csv")


@router.get("/data/applications.csv")
def export_applications(request: Request):
    with session(request) as s:
        return _attachment(applications_csv(s), "text/csv; charset=utf-8", "applications.csv")
