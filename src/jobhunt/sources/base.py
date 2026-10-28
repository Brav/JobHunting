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
    is_remote: bool | None = None  # JobSpy's own remote guess from title and location


@dataclass(frozen=True)
class JobPage:
    """A job's own page: the description (Markdown) and LinkedIn's details box as (label, value) pairs."""
    description: str
    details: list[tuple[str, str]]


class SourceError(Exception):
    """The search failed (network, parsing, unexpected response)."""


class SourceBlocked(SourceError):
    """The job board rate-limited or blocked the request."""


class SourceUnavailable(SourceError):
    """A temporary failure (network, server error) that says nothing about the job itself."""


class JobSource(Protocol):
    def search(self, keywords: str, location: str, radius_km: int,
               window_hours: int, limit: int) -> list[JobPosting]: ...
