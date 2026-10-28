"""What was sent with an application, and how: channel, CV, cover letter, and a copy of the file sent."""

import re

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from jobhunt.applications import CHANNELS
from jobhunt.models import Job
from jobhunt.web.common import session
from jobhunt.web.job import pane_response

router = APIRouter()

ALLOWED = {".pdf", ".docx", ".doc", ".odt", ".txt", ".md"}
MAX_BYTES = 10 * 1024 * 1024


def _safe_name(name: str) -> str:
    """Just the file's own name, letters, digits, dots, dashes and underscores: never a path."""
    base = re.split(r"[\\/]", name)[-1]
    return re.sub(r"[^\w.-]+", "_", base).strip("._")[:100] or "sent"


@router.post("/jobs/{job_id}/sent", response_class=HTMLResponse)
async def save_sent(request: Request, job_id: int, applied_via: str = Form(""), sent_cv: str = Form(""),
                    sent_tailored: str = Form(""), sent_cover_letter: str = Form(""),
                    file: UploadFile | None = File(None)):
    if applied_via and applied_via not in CHANNELS:
        raise HTTPException(status_code=400, detail="Unknown channel")
    stored = None
    if file is not None and file.filename:
        name = _safe_name(file.filename)
        if not any(name.lower().endswith(ext) for ext in ALLOWED):
            raise HTTPException(status_code=400, detail="PDF, Word, ODT, text or Markdown files only")
        content = await file.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise HTTPException(status_code=400, detail="Files up to 10 MB")
        folder = request.app.state.settings.db_path.parent / "sent" / str(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
        stored = f"sent/{job_id}/{name}"
    with session(request) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job.applied_via = applied_via or None
        job.sent_cv = " ".join(sent_cv.split())[:200] or None
        job.sent_tailored, job.sent_cover_letter = sent_tailored == "1", sent_cover_letter == "1"
        job.sent_file = stored or job.sent_file
        s.add(job)
        s.commit()
    return pane_response(request, job_id)


@router.get("/jobs/{job_id}/sent-file")
def sent_file(request: Request, job_id: int):
    with session(request) as s:
        job = s.get(Job, job_id)
    if job is None or not job.sent_file:
        raise HTTPException(status_code=404, detail="Nothing stored")
    path = (request.app.state.settings.db_path.parent / job.sent_file).resolve()
    folder = (request.app.state.settings.db_path.parent / "sent").resolve()
    if folder not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Nothing stored")
    return FileResponse(path, filename=path.name)
