"""FastAPI application factory and the run-control routes."""

import logging
import time
from dataclasses import replace
from datetime import datetime
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from jobhunt.backup import ensure_recent_backup
from jobhunt.config import Settings
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.scheduler import RunCoordinator
from jobhunt.job_listing import clear_service_translation_errors
from jobhunt.sources.base import JobPage, JobSource
from jobhunt.translate import DeepLTranslator, NoTranslator, Translator
from jobhunt.web import applications, blocks, companies, contacts, cv, data, digest, eligibility, sent, focus, job, listings, report, stats, tracker
from jobhunt.web import settings as settings_page  # `settings` is create_app's Settings
from jobhunt.web.common import render


def _default_skills_from_cv(session_factory, settings: Settings) -> None:
    """First start: the user's skills are the stack terms in their CV (read only); they edit them in Settings."""
    from jobhunt import checks
    from jobhunt.cv import read_cv_text
    from jobhunt.fit import default_skills
    from jobhunt.preferences import load_preferences, save_preferences
    from jobhunt.timeutil import utcnow
    with session_factory() as s:
        prefs = load_preferences(s)
        if prefs.skills:
            return
        try:
            skills = default_skills(read_cv_text(settings.cv_path))
        except Exception:
            return  # no readable CV: fit stays empty until the user lists skills
        if skills:
            save_preferences(s, replace(prefs, skills=skills), utcnow())
            checks.refresh_all(s)


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
    _default_skills_from_cv(session_factory, settings)
    if source is None:
        from jobhunt.sources.linkedin import LinkedInSource
        source = LinkedInSource()
    if fetch_page is None:
        from jobhunt.sources.linkedin import fetch_job_page as fetch_page
    if translator is None:
        translator = DeepLTranslator(settings.deepl_auth_key) if settings.deepl_auth_key else NoTranslator()
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

    app = FastAPI(title="Job tracker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.coordinator = coordinator
    app.state.fetch_page = fetch_page
    app.state.translator = translator
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
    app.include_router(settings_page.router)
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
