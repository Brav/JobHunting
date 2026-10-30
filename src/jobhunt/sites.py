"""The sites a listing can search: the built-in ones, and the user's own (feeds and watched companies)."""

import json
from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, select

from jobhunt.models import CustomFeed, Listing, WatchedCompany
from jobhunt.preferences import load_preferences
from jobhunt.timeutil import utcnow
from jobhunt.watchlist import ATS, board_page_url


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    description: str
    per_keywords: bool = False  # searched once per keyword, not read whole and matched


BUILTIN = {s.key: s for s in (
    Site("linkedin", "LinkedIn", "Jobs near the listing's location, within its radius, through JobSpy."),
    Site("remoteok", "Remote OK", "Remote jobs worldwide: its whole feed, matched to your keywords."),
    Site("himalayas", "Himalayas", "Remote jobs, searched with your keywords.", True),
    Site("hn", "Hacker News", "This month's “Who is hiring” thread, matched to your keywords."),
    Site("devbg", "DEV.BG", "IT jobs in Bulgaria, searched with your keywords.", True),
    Site("infostud", "HelloWorld/Infostud",
         "Jobs in Serbia from the HelloWorld.rs and Infostud feeds, matched to your keywords."),
    Site("4dayweek", "4 Day Week",
         "Jobs with shorter weeks, mostly remote, searched with your keywords and home country.", True),
)}
# Job.source → label
SOURCE_LABELS = {**{k: s.label for k, s in BUILTIN.items()}, "feed": "Your feeds", **ATS,
                 "capture": "the original site"}


@dataclass(frozen=True)
class OwnSite:
    key: str  # feed:<id> or board:<id>
    name: str
    kind: str  # "feed", or the hiring system's key
    url: str
    token: str = ""


def own_sites(session: Session) -> list[OwnSite]:
    """The user's feeds by name, then their watched companies by name."""
    feeds = session.exec(select(CustomFeed).order_by(CustomFeed.name)).all()
    boards = session.exec(select(WatchedCompany).order_by(WatchedCompany.name)).all()
    return [*(OwnSite(f"feed:{f.id}", f.name, "feed", f.url) for f in feeds),
            *(OwnSite(f"board:{w.id}", w.name, w.ats, board_page_url(w.ats, w.token), w.token) for w in boards)]


def parse_sites(stored: str | None, known: Iterable[str]) -> tuple[str, ...] | None:
    """A listing's stored list without keys that no longer exist; None when nothing is left."""
    try:
        keys = json.loads(stored) if stored else None
    except ValueError:
        return None
    if not isinstance(keys, list):
        return None
    known = set(known)
    kept = tuple(k for k in keys if isinstance(k, str) and k in known)
    return kept or None


def sites_for(session: Session, listing: Listing) -> tuple[str, ...]:
    """The site keys this listing searches."""
    own = [o.key for o in own_sites(session)]
    chosen = parse_sites(listing.sites, [*BUILTIN, *own])
    if chosen:
        return chosen
    return (*(k for k in load_preferences(session).sites if k in BUILTIN), *own)


def site_labels(session: Session) -> dict[str, str]:
    return {**{k: s.label for k, s in BUILTIN.items()}, **{o.key: o.name for o in own_sites(session)}}


def remove_site(session: Session, key: str) -> int:
    """Delete an own site and drop it from every listing; returns how many listings went back to Default."""
    kind, _, raw = key.partition(":")
    row = session.get(CustomFeed if kind == "feed" else WatchedCompany, int(raw)) if raw.isdecimal() else None
    reset = 0
    for listing in session.exec(select(Listing).where(Listing.sites.is_not(None))).all():
        try:
            keys = json.loads(listing.sites)
        except ValueError:
            continue
        if not isinstance(keys, list) or key not in keys:
            continue
        left = [k for k in keys if k != key]
        listing.sites = json.dumps(left) if left else None
        if not left:
            listing.search_changed_at = utcnow()  # back on Default: it may gain LinkedIn
            reset += 1
        session.add(listing)
    if row is not None:
        session.delete(row)
    session.commit()
    return reset
