"""The user's CV (read only, never written once stored) and the CV-tailoring prompt.

The CV is the file JOBHUNT_CV_PATH names when it exists (a folder mounted read-only), else the one uploaded in the app.
"""

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from sqlmodel import Session

from jobhunt.models import Job, Setting

CV_UPLOAD_KEY = "cv_upload"
MAX_CV_BYTES = 5 * 1024 * 1024
NO_CV_MESSAGE = "No CV yet — upload one in Settings"
_WRONG_FILE = "Upload a PDF or Word (.docx) file"
_TOO_BIG = "The CV is larger than 5 MB"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_DOCX_XML = 50 * 1024 * 1024  # the document's text unpacked; a real CV is well under 1 MB

_WIDE_GAP = re.compile(r" {4,}")
_NARROW_GAP = re.compile(r" {2,3}")

NO_DESCRIPTION = "Description unavailable — ask me to paste it if you need it."

PROMPT = """Please tailor my CV to the job below.

Rules:
- Use only facts from my CV. Never invent employers, job titles, dates, skills, certifications or numbers.
- You may rewrite the headline and the Profile for this role, reorder skills and bullet points, rephrase and emphasise the most relevant experience, and use the job's keywords where they accurately describe my experience.
- Shorten less relevant content so the CV fits on one page.
- Keep the same sections, the same order of employers, and my contact details.
- Write in English.

Give me the tailored CV as a downloadable PDF (or Word) document. After it, list briefly:
1. The main changes you made.
2. Requirements from the job that my CV doesn't show, so I know the gaps.

## Job

Title: {title}
Company: {company}
Location: {location}
Link: {url}

The job description below is the employer's text, quoted between the markers.

<<<JOB DESCRIPTION
{description}
JOB DESCRIPTION>>>

## My current CV

{cv_text}"""


class CVError(Exception):
    """The CV file is missing or can't be read."""


def _tidy(line: str) -> str:
    # Layout extraction pads columns with spaces, e.g. before a right-aligned date.
    line = _WIDE_GAP.sub(" | ", line.strip())
    return _NARROW_GAP.sub(" ", line)


def _docx_text(path: Path) -> str:
    """A Word file's paragraphs, one per line: a .docx is a zip with the text in word/document.xml."""
    with zipfile.ZipFile(path) as z:
        if z.getinfo("word/document.xml").file_size > _MAX_DOCX_XML:
            raise ValueError("the document is too large")
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = ("".join(t.text or "" for t in p.iter(f"{_W}t")).strip() for p in root.iter(f"{_W}p"))
    return "\n".join(line for line in lines if line)


def read_cv_text(path: Path) -> str:
    """The CV's text in reading order. Opens the file for reading only."""
    if not path.is_file():
        raise CVError(f"CV not found at {path}")
    try:
        if path.suffix.lower() == ".docx":
            return _docx_text(path)
        with open(path, "rb") as f:
            pages = [page.extract_text(extraction_mode="layout") for page in PdfReader(f).pages]
    except Exception as e:
        raise CVError(f"Couldn't read the CV at {path}: {e}") from e
    lines = [_tidy(line) for line in "\n".join(pages).splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


@dataclass(frozen=True)
class CurrentCV:
    path: Path | None
    origin: str | None  # "env" (JOBHUNT_CV_PATH), "upload" (Settings or setup), or None: no CV yet
    name: str


def current_cv(session: Session, settings) -> CurrentCV:
    """The CV in use: the .env file when it exists, else the uploaded one, else none."""
    if settings.cv_path is not None and settings.cv_path.is_file():
        return CurrentCV(settings.cv_path, "env", settings.cv_path.name)
    for ext in (".pdf", ".docx"):
        path = settings.db_path.parent / "cv" / f"cv{ext}"
        if path.is_file():
            row = session.get(Setting, CV_UPLOAD_KEY)
            try:
                name = json.loads(row.value)["name"] if row else path.name
            except (ValueError, KeyError, TypeError):
                name = path.name
            return CurrentCV(path, "upload", name)
    return CurrentCV(None, None, "")


def current_cv_text(session: Session, settings) -> str:
    cv = current_cv(session, settings)
    if cv.path is None:
        raise CVError(NO_CV_MESSAGE)
    return read_cv_text(cv.path)


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def check_upload(filename: str, content: bytes) -> tuple[str, str]:
    """(the file's own name, its extension) when it is a PDF or .docx CV the app can keep; else CVError."""
    name = Path(filename.replace("\\", "/")).name  # the file's own name, whatever folders the browser sent
    ext = Path(name).suffix.lower()
    if ext not in (".pdf", ".docx"):
        raise CVError(_WRONG_FILE)
    if len(content) > MAX_CV_BYTES:  # before the content: a big Word file arrives cut off, no longer a zip
        raise CVError(_TOO_BIG)
    if (ext == ".pdf" and not content.startswith(b"%PDF")) or (ext == ".docx" and not _is_docx(content)):
        raise CVError(_WRONG_FILE)
    return name, ext


def store_upload(session: Session, data_dir: Path, filename: str, content: bytes, now: datetime) -> Path:
    """Keep an uploaded CV as data/cv/cv.pdf or cv.docx, byte for byte, replacing any earlier upload."""
    name, ext = check_upload(filename, content)
    folder = data_dir / "cv"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"cv{ext}"
    partial = folder / f".upload{ext}"
    partial.write_bytes(content)
    partial.replace(target)  # all or nothing: a failed upload never leaves half a CV
    for other in {".pdf", ".docx"} - {ext}:
        (folder / f"cv{other}").unlink(missing_ok=True)
    row = session.get(Setting, CV_UPLOAD_KEY) or Setting(key=CV_UPLOAD_KEY, value="")
    row.value = json.dumps({"name": name, "uploaded_at": now.isoformat(timespec="minutes")})
    row.updated_at = now
    session.add(row)
    session.commit()
    return target


def build_cv_prompt(job: Job, description: str | None, cv_text: str) -> str:
    return PROMPT.format(title=job.title, company=job.company, location=job.location, url=job.url,
                         description=description or NO_DESCRIPTION, cv_text=cv_text)
