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
    # Set when keywords, location or radius change, so the next run searches the full 7 days again.
    search_changed_at: NaiveDatetime | None = None


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    external_id: str = Field(unique=True, index=True)
    # Normalised title + company (jobhunt.dedup.match_key); reposts and other listings' finds map to this job.
    match_key: str | None = Field(default=None, index=True)
    title: str
    company: str
    location: str
    url: str
    posted_at: date | None = None
    first_seen_at: NaiveDatetime = Field(default_factory=utcnow)
    status: str = "new"
    status_changed_at: NaiveDatetime | None = None
    description: str | None = None  # original-language Markdown from the job's LinkedIn page
    description_fetched_at: NaiveDatetime | None = None
    details: str | None = None  # JSON list of [label, value] from LinkedIn's details box
    description_error: str | None = None  # why the last download failed (not "blocked")
    language: str | None = None  # ISO 639-1 code of the description, e.g. "de"
    title_en: str | None = None
    description_en: str | None = None
    translation_error: str | None = None
    workplace: str | None = Field(default=None, index=True)  # remote / hybrid / on-site; None = unknown
    seniority: str | None = Field(default=None, index=True)  # from LinkedIn's details box
    employment_type: str | None = Field(default=None, index=True)
    seen_at: NaiveDatetime | None = None  # first time it was shown in a detail pane or focus mode
    stage: str | None = Field(default=None, index=True)  # current application stage; None unless applied
    # jobhunt.eligibility: the automatic verdict, its reasons (JSON), the user's override, the rules version used
    eligibility: str | None = Field(default=None, index=True)
    eligibility_reasons: str | None = None
    eligibility_override: str | None = None
    eligibility_rules: int | None = None
    # jobhunt.signals: what LinkedIn's page says about the posting, and how often it has come back
    applicants: int | None = None
    applicants_text: str | None = None
    closed_at: NaiveDatetime | None = None  # LinkedIn says it no longer accepts applications
    apply_url: str | None = None  # the employer's own application page
    salary_source_text: str | None = None  # LinkedIn's salary box, as shown
    reposts: int | None = None
    ghost_risk: int | None = Field(default=None, index=True)  # 0–3; 2+ = likely ghost
    ghost_reasons: str | None = None  # JSON list of short reasons
    blocked_by: str | None = None  # jobhunt.blocks: why the user's rules hide it ("Company blocked")
    hide_reason: str | None = None  # why the user hid it (jobhunt.blocks.HIDE_REASONS)
    scam_flags: str | None = None  # jobhunt.scam: JSON list of warning signs, None when there are none
    salary_min_eur: int | None = None  # jobhunt.salary: yearly, in euros
    salary_max_eur: int | None = Field(default=None, index=True)
    salary_text: str | None = None  # as written in the job
    # What the user sent and how (jobhunt.applications.CHANNELS); the file is a copy under data/sent/<job id>/
    applied_via: str | None = None
    sent_cv: str | None = None
    sent_tailored: bool | None = None
    sent_cover_letter: bool | None = None
    sent_file: str | None = None  # relative to the data directory


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
    job_id: int = Field(foreign_key="jobs.id", primary_key=True, index=True)
    is_new: bool


class ApplicationStep(SQLModel, table=True):
    """One dated stage of an application (jobhunt.applications); jobs with steps are never deleted."""

    __tablename__ = "application_steps"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    stage: str
    on_date: date
    note: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    """App settings as JSON values, e.g. the eligibility profile."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class Sighting(SQLModel, table=True):
    """A job seen in a run's results: when, under which LinkedIn id, and the posting date shown then."""

    __tablename__ = "sightings"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True, ondelete="CASCADE")  # history goes with its job
    seen_at: NaiveDatetime
    external_id: str
    posted_at: date | None = None


class BlockedCompany(SQLModel, table=True):
    """A company whose new jobs the user never wants to see."""

    __tablename__ = "blocked_companies"

    key: str = Field(primary_key=True)  # jobhunt.blocks.company_key
    name: str
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class NegativeRule(SQLModel, table=True):
    """A word or phrase that hides new jobs: in the title only, or anywhere in the title and description."""

    __tablename__ = "negative_rules"

    id: int | None = Field(default=None, primary_key=True)
    phrase: str
    scope: str = "title"  # "title" or "anywhere"
    active: bool = True
    created_at: NaiveDatetime = Field(default_factory=utcnow)
