"""FastAPI application factory and the run-control routes."""

import logging
import secrets
import time
from dataclasses import replace
from datetime import datetime
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from jobhunt.backup import ensure_recent_backup, hold_app_lock
from jobhunt.config import Settings
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.scheduler import RunCoordinator
from jobhunt.setup import setup_state
from jobhunt.job_listing import clear_service_translation_errors
from jobhunt.sources.base import JobPage, JobSource
from jobhunt.deepl_key import load_key
from jobhunt.translate import DeepLTranslator, NoTranslator, SwitchableTranslator, Translator
from jobhunt.web import applications, blocks, capture, companies, contacts, cv, data, digest, eligibility, email_update, sent, focus, job, listings, report, stats, tracker
from jobhunt.web import settings as settings_page  # `settings` is create_app's Settings
from jobhunt.web import setup
from jobhunt.web.common import render


REDIRECT_EXEMPT = ("/setup", "/static/", "/capture", "/data/", "/runs/")  # reachable while setup is pending


def make_translator(key: str) -> Translator:
    return DeepLTranslator(key) if key else NoTranslator()


def _default_skills_from_cv(session_factory, settings: Settings) -> None:
    """First start: the user's skills are the stack terms in their CV (read only); they edit them in Settings."""
    from jobhunt.cv import current_cv
    from jobhunt.setup import fill_skills_from_cv
    with session_factory() as s:
        fill_skills_from_cv(s, current_cv(s, settings).path)


def create_app(settings: Settings, *, source: JobSource | None = None,
               fetch_page: Callable[[str], JobPage] | None = None, translator: Translator | None = None,
               start_scheduler: bool = True, sleep: Callable[[float], None] = time.sleep) -> FastAPI:
    if settings.db_path.exists():  # a copy before startup changes anything (new columns, re-checks)
        try:
            ensure_recent_backup(settings.db_path, settings.db_path.parent / "backups",
                                 datetime.now(settings.tz).replace(tzinfo=None))
        except Exception:
            logging.getLogger(__name__).exception("startup backup failed")
    engine = make_engine(settings.db_path)
    init_db(engine, settings.tz)
    session_factory = make_session_factory(engine)
    with session_factory() as s:
        from jobhunt.setup import adopt_legacy_profile, time_zone
        from jobhunt.timeutil import utcnow
        if adopt_legacy_profile(s, utcnow()):
            from jobhunt import checks
            checks.refresh_all(s)  # jobs re-checked at start before the profile existed got "Finish setup"
        settings = replace(settings, tz=time_zone(s, settings.tz))  # the user's zone; TZ only until setup
    _default_skills_from_cv(session_factory, settings)
    if source is None:
        import requests
        from jobhunt.eligibility import load_profile
        from jobhunt.sites import own_sites
        from jobhunt.sources.combined import CombinedSource
        from jobhunt.sources.linkedin import LinkedInSource

        def own() -> list:
            with session_factory() as s:
                return own_sites(s)
        def home_country() -> str:
            with session_factory() as s:
                return load_profile(s).home_country
        source = CombinedSource(LinkedInSource(), get=requests.get, own=own, home_country=home_country)
    if hasattr(source, "on_errors"):  # the extra feeds: keep their failures for Settings and the report
        def keep_feed_errors(errors: dict[str, str]) -> None:
            from jobhunt.preferences import save_feed_errors
            from jobhunt.timeutil import utcnow
            with session_factory() as s:
                save_feed_errors(s, errors, utcnow())
        source.on_errors = keep_feed_errors
    if fetch_page is None:
        from jobhunt.sources.linkedin import fetch_job_page as fetch_page
    if translator is None:  # the .env key wins; else the one saved in Settings
        translator = make_translator(settings.deepl_auth_key or load_key(settings.db_path.parent))
    translator = SwitchableTranslator(translator)
    clear_service_translation_errors(session_factory)  # stored by older versions; retried from now on
    coordinator = RunCoordinator(session_factory, source, settings, sleep=sleep, fetch_page=fetch_page,
                                 translator=translator)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_scheduler:
            coordinator.start()
        try:
            yield
        finally:
            coordinator.shutdown()
            if _app.state.app_lock is not None:
                _app.state.app_lock.close()

    app = FastAPI(title="Job tracker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    with session_factory() as s:
        app.state.setup_pending = setup_state(s) == "pending"

    @app.middleware("http")
    async def setup_first(request: Request, call_next):
        """A new install opens on the setup page until it's saved or skipped (the bookmarklet and downloads still work)."""
        if (app.state.setup_pending and request.method == "GET" and request.headers.get("HX-Request") != "true"
                and not request.url.path.startswith(REDIRECT_EXEMPT)):  # htmx fetches parts of a page, never pages
            with session_factory() as s:
                app.state.setup_pending = setup_state(s) == "pending"
            if app.state.setup_pending:
                return RedirectResponse("/setup", status_code=303)
        return await call_next(request)

    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver", urlsplit(settings.public_url).hostname or ""}

    @app.middleware("http")
    async def same_site_only(request: Request, call_next):
        """Only this machine's own addresses (no DNS rebinding), and no writes posted from another site — except the
        bookmarklet's job page into the capture form, which only shows it for the user to check."""
        host = request.headers.get("host", "")
        if urlsplit(f"//{host}").hostname not in allowed_hosts:
            return PlainTextResponse("Unknown host", status_code=400)
        origin = request.headers.get("origin")
        if (request.method not in ("GET", "HEAD", "OPTIONS") and origin is not None
                and urlsplit(origin).netloc != host and request.url.path != "/capture"):
            return PlainTextResponse("Requests from other sites are refused", status_code=403)
        return await call_next(request)

    app.state.settings = settings
    app.state.app_lock = hold_app_lock(settings.db_path)  # a restore refuses while this is held
    app.state.session_factory = session_factory
    app.state.coordinator = coordinator
    app.state.source = source
    import requests as _requests
    app.state.http_get = _requests.get  # board detection; tests swap in a fake
    app.state.fetch_page = fetch_page
    app.state.form_token = secrets.token_urlsafe(24)  # forms that write what another site sent (capture, email)
    app.state.translator = translator
    app.state.translator_factory = make_translator  # Settings → Translation builds the new one; tests swap it
    app.include_router(report.router)
    app.include_router(listings.router)
    app.include_router(tracker.router)
    app.include_router(cv.router)
    app.include_router(job.router)
    app.include_router(focus.router)
    app.include_router(applications.router)
    app.include_router(stats.router)
    app.include_router(eligibility.router)
    app.include_router(data.router)
    app.include_router(blocks.router)
    app.include_router(sent.router)
    app.include_router(companies.router)
    app.include_router(contacts.router)
    app.include_router(digest.router)
    app.include_router(capture.router)
    app.include_router(email_update.router)
    app.include_router(settings_page.router)
    app.include_router(setup.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.post("/runs", response_class=HTMLResponse)
    def run_now(request: Request):
        coordinator.trigger("manual")
        return render(request, "_status_bar.html")

    @app.get("/runs/status", response_class=HTMLResponse)
    def run_status(request: Request):
        response = render(request, "_status_bar.html")
        if not coordinator.is_running:
            response.headers["HX-Refresh"] = "true"  # only a running bar polls: reload to show the new jobs
        return response

    return app
