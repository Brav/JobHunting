"""Filtering, sorting and counting jobs for the report and All jobs, driven by URL query parameters."""

from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, func, not_, or_
from sqlmodel import Session, select

from jobhunt.eligibility import VERDICT_LABELS, VERDICTS
from jobhunt.language import language_name
from jobhunt.signals import LIKELY_GHOST
from jobhunt.models import Job, Listing, Match, Run, RunListing
from jobhunt.timeutil import local_day_bounds_utc
from jobhunt.workplace import WORKPLACES

TABS = ("active", "undecided", "saved", "applied", "hidden", "all")
SORTS = ("found", "posted", "company")
WITHIN = {"24h": 1, "3d": 3, "7d": 7, "30d": 30}  # days
WITHIN_LABELS = {"24h": "last 24 h", "3d": "last 3 days", "7d": "last 7 days", "30d": "last 30 days"}
WORKPLACE_FILTERS = (*WORKPLACES, "unknown")
WORKPLACE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "unknown": "Workplace unknown"}
_TAB_STATUSES = {"undecided": "new", "saved": "interested", "applied": "applied", "hidden": "hidden"}
_QUERY_KEYS = {"listing_id": "listing"}  # URL name when it differs from the field name


# The verdict that counts (jobhunt.eligibility.effective, as SQL): the user's override, else the automatic one.
EFFECTIVE_ELIGIBILITY = func.coalesce(Job.eligibility_override, Job.eligibility, "check")
# Undecided jobs the user can't take are left out unless the eligibility filter asks for them.
HIDDEN_BY_DEFAULT = and_(Job.status == "new", EFFECTIVE_ELIGIBILITY == "not_eligible")


def _lower(column):
    # py_lower is registered in db.make_engine; SQLite's lower() ignores non-ASCII letters.
    return func.py_lower(column, type_=String)


@dataclass(frozen=True)
class JobFilters:
    tab: str = "active"
    listing_id: int | None = None
    workplace: tuple[str, ...] = ()
    eligibility: tuple[str, ...] = ()  # none = everything except jobs hidden by default
    ghost: str = ""  # "hide" leaves out likely ghost jobs
    language: str = ""  # "", "en", "non-en" or an ISO 639-1 code
    seniority: str = ""
    employment_type: str = ""
    company: str = ""
    found_within: str = ""
    posted_within: str = ""
    q: str = ""
    sort: str = "found"

    @classmethod
    def from_query(cls, params) -> "JobFilters":
        """Read filters from query parameters; anything unknown falls back to the default."""
        def get(key: str) -> str:
            return (params.get(key) or "").strip()

        listing = get("listing")
        return cls(
            tab=get("tab") if get("tab") in TABS else "active",
            listing_id=int(listing) if listing.isdecimal() else None,
            workplace=tuple(dict.fromkeys(v for v in params.getlist("workplace") if v in WORKPLACE_FILTERS)),
            eligibility=tuple(dict.fromkeys(v for v in params.getlist("eligibility") if v in VERDICTS)),
            ghost="hide" if get("ghost") == "hide" else "",
            language=get("language"), seniority=get("seniority"), employment_type=get("employment_type"),
            company=get("company"),
            found_within=get("found_within") if get("found_within") in WITHIN else "",
            posted_within=get("posted_within") if get("posted_within") in WITHIN else "",
            q=get("q"), sort=get("sort") if get("sort") in SORTS else "found",
        )

    def query(self, **overrides) -> str:
        """The URL query for these filters (defaults left out), with `overrides` applied."""
        f = replace(self, **overrides)
        pairs = []
        for field in fields(f):
            value = getattr(f, field.name)
            if value == field.default:
                continue
            key = _QUERY_KEYS.get(field.name, field.name)
            pairs.extend((key, v) for v in value) if isinstance(value, tuple) else pairs.append((key, value))
        return urlencode(pairs)

    def chips(self) -> list[tuple[str, str]]:
        """(label, query without that filter) for each active filter except tab, sort and listing."""
        chips = [(WORKPLACE_LABELS[w], self.query(workplace=tuple(x for x in self.workplace if x != w)))
                 for w in self.workplace]
        chips += [(VERDICT_LABELS[v], self.query(eligibility=tuple(x for x in self.eligibility if x != v)))
                  for v in self.eligibility]
        if self.ghost:
            chips.append(("Hiding likely ghost jobs", self.query(ghost="")))
        if self.language:
            label = {"en": "English", "non-en": "Not English"}.get(self.language) or language_name(self.language)
            chips.append((label, self.query(language="")))
        for name in ("seniority", "employment_type"):
            if getattr(self, name):
                chips.append((getattr(self, name), self.query(**{name: ""})))
        if self.company:
            chips.append((f"Company: {self.company}", self.query(company="")))
        if self.found_within:
            chips.append((f"Found: {WITHIN_LABELS[self.found_within]}", self.query(found_within="")))
        if self.posted_within:
            chips.append((f"Posted: {WITHIN_LABELS[self.posted_within]}", self.query(posted_within="")))
        if self.q:
            chips.append((f"“{self.q}”", self.query(q="")))
        return chips


def apply_filters(stmt, f: JobFilters, now: datetime, *, include_tab: bool = True):
    if include_tab and f.tab == "active":
        stmt = stmt.where(Job.status != "hidden")
    elif include_tab and f.tab in _TAB_STATUSES:
        stmt = stmt.where(Job.status == _TAB_STATUSES[f.tab])
    if f.listing_id is not None:
        stmt = stmt.where(Job.id.in_(select(Match.job_id).where(Match.listing_id == f.listing_id)))
    if f.workplace:
        known = [w for w in f.workplace if w != "unknown"]
        conditions = [Job.workplace.in_(known)] if known else []
        if "unknown" in f.workplace:
            conditions.append(Job.workplace.is_(None))
        stmt = stmt.where(or_(*conditions))
    if f.eligibility:
        stmt = stmt.where(EFFECTIVE_ELIGIBILITY.in_(f.eligibility))
    else:
        stmt = stmt.where(not_(HIDDEN_BY_DEFAULT))
    if f.ghost == "hide":
        stmt = stmt.where(or_(Job.ghost_risk.is_(None), Job.ghost_risk < LIKELY_GHOST))
    if f.language == "non-en":
        stmt = stmt.where(Job.language.is_not(None), Job.language != "en")
    elif f.language:
        stmt = stmt.where(Job.language == f.language)
    if f.seniority:
        stmt = stmt.where(Job.seniority == f.seniority)
    if f.employment_type:
        stmt = stmt.where(Job.employment_type == f.employment_type)
    if f.company:
        stmt = stmt.where(_lower(Job.company).contains(f.company.lower(), autoescape=True))
    if f.found_within:
        stmt = stmt.where(Job.first_seen_at >= now - timedelta(days=WITHIN[f.found_within]))
    if f.posted_within:
        stmt = stmt.where(Job.posted_at >= (now - timedelta(days=WITHIN[f.posted_within])).date())
    if f.q:
        needle = f.q.lower()
        stmt = stmt.where(or_(*(_lower(column).contains(needle, autoescape=True)
                                for column in (Job.title, Job.title_en, Job.company))))
    return stmt


def ordered(stmt, sort: str):
    if sort == "posted":
        return stmt.order_by(Job.posted_at.is_(None), Job.posted_at.desc(), Job.id.desc())
    if sort == "company":
        return stmt.order_by(_lower(Job.company), Job.id)
    return stmt.order_by(Job.first_seen_at.desc(), Job.id.desc())


def _count(session: Session, stmt) -> int:
    return session.exec(select(func.count()).select_from(stmt.subquery())).one()


def _after(stmt, sort: str, last: Job):
    """Rows that come after `last` in `sort` order (keyset paging: deciding on jobs doesn't shift pages)."""
    if sort == "posted":
        if last.posted_at is None:
            return stmt.where(Job.posted_at.is_(None), Job.id < last.id)
        return stmt.where(or_(Job.posted_at.is_(None), Job.posted_at < last.posted_at,
                              (Job.posted_at == last.posted_at) & (Job.id < last.id)))
    if sort == "company":
        key = last.company.lower()
        return stmt.where(or_(_lower(Job.company) > key, (_lower(Job.company) == key) & (Job.id > last.id)))
    return stmt.where(or_(Job.first_seen_at < last.first_seen_at,
                          (Job.first_seen_at == last.first_seen_at) & (Job.id < last.id)))


def filtered_jobs(session: Session, f: JobFilters, now: datetime, *, offset: int = 0, limit: int = 50,
                  cursor: int | None = None) -> tuple[list[Job], int]:
    """A page of jobs and the total count. `cursor` is the id of the last job already shown."""
    stmt = apply_filters(select(Job), f, now)
    page = stmt
    last = session.get(Job, cursor) if cursor is not None else None
    if last is not None:
        page = _after(stmt, f.sort, last)
    jobs = session.exec(ordered(page, f.sort).offset(offset).limit(limit)).all()
    return list(jobs), _count(session, stmt)


def ordered_ids(session: Session, f: JobFilters, now: datetime) -> list[tuple[int, str]]:
    """(id, status) of every matching job in list order — light enough for thousands of jobs."""
    stmt = apply_filters(select(Job.id, Job.status), f, now)
    return [(job_id, status) for job_id, status in session.exec(ordered(stmt, f.sort)).all()]


def hidden_ineligible_count(session: Session, f: JobFilters, now: datetime) -> int:
    """How many jobs matching these filters the default leaves out (0 once the eligibility filter is set)."""
    if f.eligibility:
        return 0
    return _count(session, apply_filters(select(Job), replace(f, eligibility=VERDICTS), now).where(HIDDEN_BY_DEFAULT))


def tab_counts(session: Session, f: JobFilters, now: datetime) -> dict[str, int]:
    return {tab: _count(session, apply_filters(select(Job), replace(f, tab=tab), now)) for tab in TABS}


@dataclass
class FilterOptions:
    languages: list[str]
    seniorities: list[str]
    employment_types: list[str]


def filter_options(session: Session) -> FilterOptions:
    def distinct(column) -> list[str]:
        return [v for v in session.exec(select(column).where(column.is_not(None)).distinct()).all() if v]

    return FilterOptions(languages=sorted(distinct(Job.language), key=language_name),
                         seniorities=sorted(distinct(Job.seniority)),
                         employment_types=sorted(distinct(Job.employment_type)))


def company_names(session: Session, q: str, limit: int = 20) -> list[str]:
    stmt = select(Job.company).where(Job.company != "").distinct()
    if q.strip():
        stmt = stmt.where(_lower(Job.company).contains(q.strip().lower(), autoescape=True))
    return list(session.exec(stmt.order_by(_lower(Job.company)).limit(limit)).all())


# --- the daily report -------------------------------------------------------------------------------------------

@dataclass
class ReportTab:
    listing: Listing | None  # None = all listings
    count: int
    error: str | None


def _run_ids(session: Session, day: date, tz: ZoneInfo) -> list[int]:
    start, end = local_day_bounds_utc(day, tz)
    return list(session.exec(select(Run.id).where(Run.started_at >= start, Run.started_at < end)
                             .order_by(Run.started_at, Run.id)).all())


def _new_jobs(run_ids: list[int], listing_id: int | None = None, include_hidden: bool = False,
              include_ineligible: bool = False):
    """Jobs first found (is_new) by these runs, optionally by one listing; hidden jobs, and jobs hidden by default
    for eligibility, left out unless asked."""
    matches = select(Match.job_id).where(Match.run_id.in_(run_ids), Match.is_new == True)  # noqa: E712
    if listing_id is not None:
        matches = matches.where(Match.listing_id == listing_id)
    stmt = select(Job).where(Job.id.in_(matches))
    if not include_ineligible:
        stmt = stmt.where(not_(HIDDEN_BY_DEFAULT))
    return stmt if include_hidden else stmt.where(Job.status != "hidden")


def report_tabs(session: Session, day: date, tz: ZoneInfo) -> list[ReportTab]:
    run_ids = _run_ids(session, day, tz)
    if not run_ids:
        return []
    order = {run_id: i for i, run_id in enumerate(run_ids)}
    latest: dict[int, RunListing] = {}
    for row in sorted(session.exec(select(RunListing).where(RunListing.run_id.in_(run_ids))),
                      key=lambda r: order[r.run_id]):
        latest[row.listing_id] = row
    tabs = [ReportTab(listing=None, count=_count(session, _new_jobs(run_ids)), error=None)]
    for listing in session.exec(select(Listing).where(Listing.id.in_(latest)).order_by(Listing.id)):
        tabs.append(ReportTab(listing=listing, count=_count(session, _new_jobs(run_ids, listing.id)),
                              error=latest[listing.id].error))
    return tabs


def report_jobs(session: Session, day: date, tz: ZoneInfo, f: JobFilters, now: datetime, *,
                unseen_only: bool = False, include_hidden: bool = False) -> list[Job]:
    run_ids = _run_ids(session, day, tz)
    if not run_ids:
        return []
    stmt = apply_filters(_new_jobs(run_ids, f.listing_id, include_hidden, include_ineligible=True),
                         replace(f, listing_id=None), now, include_tab=False)
    if unseen_only:
        stmt = stmt.where(Job.seen_at.is_(None))
    return list(session.exec(stmt.order_by(Job.posted_at.is_(None), Job.posted_at.desc(), Job.title)).all())


def unseen_count(session: Session, day: date, tz: ZoneInfo) -> int:
    run_ids = _run_ids(session, day, tz)
    return _count(session, _new_jobs(run_ids).where(Job.seen_at.is_(None))) if run_ids else 0


def report_hidden_ineligible(session: Session, day: date, tz: ZoneInfo, f: JobFilters, now: datetime) -> int:
    """How many of the day's new jobs (under these filters) the default leaves out."""
    run_ids = _run_ids(session, day, tz)
    if f.eligibility or not run_ids:
        return 0
    stmt = apply_filters(_new_jobs(run_ids, f.listing_id, include_ineligible=True),
                         replace(f, listing_id=None, eligibility=VERDICTS), now, include_tab=False)
    return _count(session, stmt.where(HIDDEN_BY_DEFAULT))
