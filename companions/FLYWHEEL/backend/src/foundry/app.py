from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import create_router
from .argus_artifact_api import create_argus_artifact_router
from .config import Settings
from .db import Database
from .flywheel_api import create_flywheel_router
from .managed_argus import ensure_managed_argus_connection
from .secrets import SecretVault
from .seeding import seed_database
from .services import BackgroundCoordinator
from .workflow_api import create_workflow_router

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    database = Database(configured.database_path)
    allowed_env_endpoints = (
        {configured.argus_token_env: configured.argus_base_url}
        if configured.argus_token_env.strip() and configured.argus_base_url.strip()
        else {}
    )
    vault = SecretVault(allowed_env_endpoints=allowed_env_endpoints)
    coordinator = BackgroundCoordinator(database, vault, configured.poll_interval_seconds, configured.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database.migrate()
        if configured.auto_seed:
            try:
                seed_database(database, configured.seed_data_dir)
            except (FileNotFoundError, ValueError) as exc:
                log.warning("Flywheel started without seed data: %s", exc)
        ensure_managed_argus_connection(
            database,
            base_url=configured.argus_base_url,
            token_env=configured.argus_token_env,
        )
        await coordinator.start()
        yield
        await coordinator.stop()

    app = FastAPI(
        title="ARGUS / FLYWHEEL API",
        version="0.2.0",
        description="Argus companion for research orchestration and immutable data provenance.",
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.db = database
    app.state.coordinator = coordinator
    app.state.secrets = vault
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled API error for %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The Flywheel backend encountered an unexpected error.",
                    "request_path": request.url.path,
                }
            },
        )

    app.include_router(create_router())
    app.include_router(create_workflow_router())
    app.include_router(create_flywheel_router())
    app.include_router(create_argus_artifact_router())

    @app.get("/health")
    def root_health() -> dict[str, object]:
        return {"ok": True, "service": "argus-research-flywheel", "role": "argus-companion"}

    return app


app = create_app()
