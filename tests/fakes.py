"""Test doubles and data helpers shared by the test suite."""

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlmodel import select

from jobhunt.models import Job, Listing, Run
from jobhunt.sources.base import JobPosting


def make_posting(external_id: str, title: str | None = None, posted_at: date | None = None,
                 is_remote: bool | None = None, company: str = "Acme", location: str = "Belgrade") -> JobPosting:
    # Default titles differ per ID: same title + company means the same job.
    return JobPosting(external_id, title or f"Engineer {external_id}", company, location,
                      f"https://www.linkedin.com/jobs/view/{external_id}", posted_at, is_remote)


class FakeClock:
    """Callable clock returning naive UTC; sleep() advances it instead of waiting."""

    def __init__(self, now: datetime):
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class FakeSource:
    """Returns scripted outcomes per keywords; the last outcome repeats. Unknown keywords find nothing."""

    def __init__(self, outcomes: dict[str, list]):
        self.outcomes = {k: list(v) for k, v in outcomes.items()}
        self.calls: list[dict] = []

    def search(self, keywords, location, radius_km, window_hours, limit):
        self.calls.append(dict(keywords=keywords, location=location, radius_km=radius_km,
                               window_hours=window_hours, limit=limit))
        queue = self.outcomes.get(keywords, [[]])
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def add_listing(sf, name: str, keywords: str = "python", location: str = "Belgrade, Serbia",
                radius_km: int = 25, active: bool = True) -> int:
    with sf() as s:
        listing = Listing(name=name, keywords=keywords, location=location, radius_km=radius_km, active=active)
        s.add(listing)
        s.commit()
        return listing.id


def add_run(sf, started_at: datetime, status: str = "ok", error: str | None = None,
            trigger: str = "manual") -> int:
    with sf() as s:
        run = Run(trigger=trigger, started_at=started_at, finished_at=started_at, status=status, error=error)
        s.add(run)
        s.commit()
        return run.id


def job_id(sf, external_id: str) -> int:
    with sf() as s:
        return s.exec(select(Job.id).where(Job.external_id == external_id)).one()


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


def make_pdf(rows: list[tuple[float, float, str]]) -> bytes:
    """A minimal one-page PDF (Helvetica 11 pt, 612x792) drawing each (x, y, text). ASCII text only."""
    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "".join(f"BT /F1 11 Tf {x} {y} Td ({escape(text)}) Tj ET\n" for x, y, text in rows).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class FakeTranslator:
    """Prefixes "EN:" instead of translating; raises `fail` when given."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def translate(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        if self.fail:
            raise self.fail
        return f"EN:{text}"
