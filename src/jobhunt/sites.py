"""The sites a listing can search: the built-in ones, and the user's own (feeds and watched companies)."""

import json
from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, select

from jobhunt.models import CustomFeed, Listing, WatchedCompany
from jobhunt.preferences import drop_feed_error, load_preferences
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


def searched_sites(session: Session) -> set[str]:
    """The site keys any active listing searches."""
    return {k for listing in session.exec(select(Listing).where(Listing.active == True)).all()  # noqa: E712
            for k in sites_for(session, listing)}


def site_labels(session: Session) -> dict[str, str]:
    return {**{k: s.label for k, s in BUILTIN.items()}, **{o.key: o.name for o in own_sites(session)}}


def remove_site(session: Session, key: str) -> int:
    """Delete an own site and its stored error, and drop it from every listing; returns how many went back to Default."""
    kind, _, raw = key.partition(":")
    table = {"feed": CustomFeed, "board": WatchedCompany}.get(kind)
    if table is None:  # only own sites can be removed
        return 0
    row = session.get(table, int(raw)) if raw.isdecimal() else None
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
    drop_feed_error(session, key, utcnow())
    session.commit()
    return reset


@dataclass(frozen=True)
class SiteChoices:
    builtin: list[tuple[str, str]]  # (key, label)
    own: list[tuple[str, str]]
    default_text: str


def default_sites_text(session: Session) -> str:
    """What "Default sites" means now, e.g. "LinkedIn, Remote OK and your 2 sites"."""
    labels = [BUILTIN[k].label for k in load_preferences(session).sites if k in BUILTIN]
    own = len(own_sites(session))
    if own:
        return f"{', '.join(labels)} and your {own} site{'s' if own != 1 else ''}"
    return f"{', '.join(labels[:-1])} and {labels[-1]}" if len(labels) > 1 else "".join(labels)


def site_choices(session: Session) -> SiteChoices:
    return SiteChoices([(k, s.label) for k, s in BUILTIN.items()],
                       [(o.key, o.name) for o in own_sites(session)], default_sites_text(session))


def sites_text(stored: str | None, labels: dict[str, str]) -> str:
    """A listing's sites for the table: "Default", or the first two names plus " +N"."""
    keys = parse_sites(stored, labels)
    if not keys:
        return "Default"
    text = ", ".join(labels[k] for k in keys[:2])
    return f"{text} +{len(keys) - 2}" if len(keys) > 2 else text


def site_form_values(session: Session, stored: str | None) -> dict:
    """The form's site mode and ticked boxes; Default shows the default sites ticked."""
    chosen = parse_sites(stored, [*BUILTIN, *(o.key for o in own_sites(session))])
    if chosen:
        return {"site_mode": "choose", "sites": list(chosen)}
    default = Listing(name="", keywords="", location="", radius_km=0)
    return {"site_mode": "default", "sites": list(sites_for(session, default))}


def sites_from_form(session: Session, mode: str, chosen: list[str]) -> tuple[str | None, str | None]:
    """(stored list, error) for the form's choice; keys come out in registry-then-own order."""
    if mode != "choose":
        return None, None
    keys = [k for k in (*BUILTIN, *(o.key for o in own_sites(session))) if k in chosen]
    if not keys:
        return None, "Tick at least one site."
    return json.dumps(keys), None
