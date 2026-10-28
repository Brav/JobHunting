"""The original CV (read only, never written) and the CV-tailoring prompt."""

import re
from pathlib import Path

from pypdf import PdfReader

from jobhunt.models import Job

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

{description}

## My current CV

{cv_text}"""


class CVError(Exception):
    """The CV file is missing or can't be read."""


def _tidy(line: str) -> str:
    # Layout extraction pads columns with spaces, e.g. before a right-aligned date.
    line = _WIDE_GAP.sub(" | ", line.strip())
    return _NARROW_GAP.sub(" ", line)


def read_cv_text(path: Path) -> str:
    """The CV's text in reading order. Opens the file for reading only."""
    if not path.is_file():
        raise CVError(f"CV not found at {path}")
    try:
        with open(path, "rb") as f:
            pages = [page.extract_text(extraction_mode="layout") for page in PdfReader(f).pages]
    except Exception as e:
        raise CVError(f"Couldn't read the CV at {path}: {e}") from e
    lines = [_tidy(line) for line in "\n".join(pages).splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


def build_cv_prompt(job: Job, description: str | None, cv_text: str) -> str:
    return PROMPT.format(title=job.title, company=job.company, location=job.location, url=job.url,
                         description=description or NO_DESCRIPTION, cv_text=cv_text)
