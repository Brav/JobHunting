"""The Sites page: the built-in sites, the owner's own feeds and careers pages, and the checks before adding one."""

import html
import re
from urllib.parse import quote_plus, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup
from sqlmodel import select

from jobhunt.blocks import company_key
from jobhunt.models import CustomFeed, WatchedCompany
from jobhunt.site_check import Refused, check_saving, check_site, normalise_url
from jobhunt.sites import BUILTIN, own_sites, remove_site
from jobhunt.watchlist import ATS
from jobhunt.web.common import render, session

router = APIRouter()

DUPLICATE = "You've already added this site."
TICK = "Tick the box to confirm the site's terms allow reading it."
BAD_KIND = "Choose a feed or a careers page by checking the address first."
BAD_TOKEN = "That careers page name isn't valid. Check the address again."
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _page(request: Request, status_code: int = 200, **ctx):
    if ctx.get("add_error"):  # a reason may quote the site's own words: escape it, but keep apostrophes readable
        ctx["add_error"] = Markup(html.escape(ctx["add_error"], quote=False))
    with session(request) as s:
        own = own_sites(s)
    return render(request, "sites.html", status_code, active="sites", builtin=list(BUILTIN.values()), own=own,
                  ats_names=ATS, **ctx)


@router.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request, added: str = "", removed: str = "", reset: int = 0):
    notice = ""
    if added:
        notice = f"Added {added}."
    elif removed:
        notice = f"Removed {removed}."
        if reset > 0:
            notice += f" {reset} listing{'s' if reset != 1 else ''} went back to Default sites."
    return _page(request, notice=notice)


@router.post("/sites/preview", response_class=HTMLResponse)
def preview_site(request: Request, url: str = Form(""), name: str = Form("")):
    """Check an address and show what it would add. Nothing is saved."""
    try:
        candidate = check_site(url, name, request.app.state.http_get, request.app.state.resolve)
    except Refused as refusal:
        return _page(request, add_error=str(refusal), form_url=url, form_name=name)
    return _page(request, preview=candidate, form_url=url, form_name=name)


@router.post("/sites/add", response_class=HTMLResponse)
def add_site(request: Request, kind: str = Form(""), url: str = Form(""), name: str = Form(""),
             token: str = Form(""), terms: str = Form("")):
    def refuse(message: str):
        return _page(request, 400, add_error=message, form_url=url, form_name=name)

    if not terms:
        return refuse(TICK)
    url = normalise_url(url)
    try:
        check_saving(url, request.app.state.http_get, request.app.state.resolve)
    except Refused as refusal:
        return refuse(str(refusal))
    if kind != "feed" and kind not in ATS:
        return refuse(BAD_KIND)
    token = token.strip()
    if kind != "feed" and not _TOKEN.fullmatch(token):
        return refuse(BAD_TOKEN)
    name = " ".join(name.split()) or urlsplit(url).hostname or url
    with session(request) as s:
        if kind == "feed":
            if s.exec(select(CustomFeed).where(CustomFeed.url == url)).first():
                return refuse(DUPLICATE)
            s.add(CustomFeed(name=name, url=url))
        else:
            if s.exec(select(WatchedCompany).where(WatchedCompany.ats == kind, WatchedCompany.token == token)).first():
                return refuse(DUPLICATE)
            existing = s.exec(select(WatchedCompany).where(WatchedCompany.company_key == company_key(name))).first()
            if existing:  # the same company: its board moves to this system, keeping its id and listings' choices
                existing.ats, existing.token = kind, token
                s.add(existing)
            else:
                s.add(WatchedCompany(company_key=company_key(name), name=name, ats=kind, token=token))
        s.commit()
    return RedirectResponse(f"/sites?added={quote_plus(name)}", status_code=303)


def _remove(request: Request, key: str, table, row_id: int):
    with session(request) as s:
        row = s.get(table, row_id)
        if row is None:
            return RedirectResponse("/sites", status_code=303)
        name = row.name
        reset = remove_site(s, key)
    return RedirectResponse(f"/sites?removed={quote_plus(name)}&reset={reset}", status_code=303)


@router.post("/sites/feeds/{feed_id}/remove")
def remove_feed(request: Request, feed_id: int):
    return _remove(request, f"feed:{feed_id}", CustomFeed, feed_id)


@router.post("/sites/boards/{board_id}/remove")
def remove_board(request: Request, board_id: int):
    return _remove(request, f"board:{board_id}", WatchedCompany, board_id)
