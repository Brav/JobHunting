"""Database tables (spec section 6). All datetimes are naive UTC."""

from datetime import date

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel

from jobhunt.timeutil import utcnow

JOB_STATUSES = ("new", "interested", "applied", "hidden")
TRIGGERS = ("scheduled", "manual", "catch_up")


class Listing(SQLModel, table=True):
    __tablename__ = "listings"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    keywords: str
    location: str
    radius_km: int = 25
    active: bool = True
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    external_id: str = Field(unique=True, index=True)
    title: str
    company: str
    location: str
    url: str
    posted_at: date | None = None
    first_seen_at: NaiveDatetime = Field(default_factory=utcnow)
    status: str = "new"
    status_changed_at: NaiveDatetime | None = None


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    trigger: str
    started_at: NaiveDatetime
    finished_at: NaiveDatetime | None = None
    status: str = "running"
    error: str | None = None


class RunListing(SQLModel, table=True):
    __tablename__ = "run_listings"

    run_id: int = Field(foreign_key="runs.id", primary_key=True)
    listing_id: int = Field(foreign_key="listings.id", primary_key=True)
    attempts: int = 0
    found: int = 0
    new: int = 0
    error: str | None = None


class Match(SQLModel, table=True):
    __tablename__ = "matches"

    run_id: int = Field(foreign_key="runs.id", primary_key=True)
    listing_id: int = Field(foreign_key="listings.id", primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", primary_key=True)
    is_new: bool
