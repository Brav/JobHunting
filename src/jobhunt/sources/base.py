"""The job source interface. The runner only knows this, never JobSpy."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class JobPosting:
    external_id: str
    title: str
    company: str
    location: str
    url: str
    posted_at: date | None


class SourceError(Exception):
    """The search failed (network, parsing, unexpected response)."""


class SourceBlocked(SourceError):
    """The job board rate-limited or blocked the request."""


class JobSource(Protocol):
    def search(self, keywords: str, location: str, radius_km: int,
               window_hours: int, limit: int) -> list[JobPosting]: ...
