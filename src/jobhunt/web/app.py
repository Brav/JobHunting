"""FastAPI application factory and the run-control routes."""

import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from jobhunt.config import Settings
from jobhunt.db import init_db, make_engine, make_session_factory
from jobhunt.scheduler import RunCoordinator
from jobhunt.sources.base import JobSource
from jobhunt.web import listings, report
from jobhunt.web.common import render


def create_app(settings: Settings, *, source: JobSource | None = None, start_scheduler: bool = True,
               sleep: Callable[[float], None] = time.sleep) -> FastAPI:
    engine = make_engine(settings.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    if source is None:
        from jobhunt.sources.linkedin import LinkedInSource
        source = LinkedInSource()
    coordinator = RunCoordinator(session_factory, source, settings, sleep=sleep)

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
    app.include_router(report.router)
    app.include_router(listings.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.post("/runs", response_class=HTMLResponse)
    def run_now(request: Request):
        coordinator.trigger("manual")
        return render(request, "_status_bar.html")

    @app.get("/runs/status", response_class=HTMLResponse)
    def run_status(request: Request):
        return render(request, "_status_bar.html")

    return app
