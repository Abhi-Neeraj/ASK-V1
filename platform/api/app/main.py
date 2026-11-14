"""Application factory. `create_app` takes explicit settings and engine so tests and tools can
build isolated instances. uvicorn serves it with `--factory`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.ai.registry import ProviderHolder
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.errors import ApiError, install_error_handlers
from app.routers import (
    audit,
    auth,
    device_api,
    devices,
    events,
    invitations,
    members,
    people,
    processing,
    recordings,
    workspace,
)
from app.security.bodylimit import BodyLimitMiddleware
from app.security.headers import SecurityHeadersMiddleware
from app.security.ratelimit import RateLimiter
from app.services.email import EmailSender, build_sender
from app.services.events import StreamRegistry
from app.services.housekeeping import run_housekeeping
from app.services.storage import Storage

logger = logging.getLogger("ask.api")


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    email: EmailSender | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)

    async def housekeeping_loop(application: FastAPI) -> None:
        while True:
            await asyncio.sleep(settings.background_interval_seconds)
            try:
                await anyio.to_thread.run_sync(
                    run_housekeeping, application.state.session_factory, settings, application.state.storage
                )
            except Exception:
                logger.exception("housekeeping run failed")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=settings.log_level)
        task = asyncio.create_task(housekeeping_loop(application)) if settings.run_background_tasks else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            engine.dispose()

    app = FastAPI(
        title="ASK API",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are a development convenience; they are not exposed in production.
        docs_url=None if settings.env == "production" else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.env == "production" else "/api/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.limiter = RateLimiter()
    app.state.streams = StreamRegistry()
    app.state.ai = ProviderHolder(settings)  # only loads a model if someone enrolls a voice
    app.state.email = email or build_sender(settings)
    if os.name != "nt":
        # Recordings and spool files are private to the service account. The default (0o022) would leave every
        # new file readable by any other account on the machine.
        os.umask(0o077)
    app.state.storage = Storage(settings.storage_dir)
    app.state.storage.remove_stale_temp_files()  # partial uploads from a previous crash

    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware, limit=settings.max_json_bytes)
    # Added last, so it is outermost: even a refused oversized request carries the headers.
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(auth.router)
    app.include_router(invitations.router)
    app.include_router(members.router)
    app.include_router(devices.router)
    app.include_router(device_api.router)
    app.include_router(events.router)
    app.include_router(recordings.router)
    app.include_router(people.router)
    app.include_router(processing.router)
    app.include_router(workspace.router)
    app.include_router(audit.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        """Liveness: the process is up. Says nothing about dependencies."""
        return {"status": "ok"}

    @app.get("/api/ready", tags=["system"])
    def ready() -> dict[str, str]:
        """Readiness: the database answers and the storage directory is usable."""
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("readiness check: database unavailable")
            raise ApiError(503, "database_unavailable", "The database is not answering.") from None
        if not app.state.storage.is_writable():
            raise ApiError(503, "storage_unavailable", "Audio storage is not writable.")
        return {"status": "ready"}

    return app


# Run with:  uvicorn app.main:create_app --factory
# Nothing is built at import time, so importing this module never needs real settings.
