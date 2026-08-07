"""FastAPI entry point for the independent PlantDiseases account service."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings
from app.db.database import Database, DatabaseHandle
from app.providers.base import ProviderAdapter, ProviderName
from app.providers.mock import build_provider_registry


def create_app(
    settings: Settings | None = None,
    providers: Mapping[ProviderName, ProviderAdapter] | None = None,
    database: DatabaseHandle | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    provider_registry = dict(providers) if providers is not None else build_provider_registry(config)
    if set(provider_registry) != {"yandex", "vk"}:
        raise ValueError("Both yandex and vk provider adapters are required")
    database_handle = database or Database(config.database_url)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        try:
            yield
        finally:
            database_handle.close()

    application = FastAPI(
        title="PlantDiseases Account API",
        version="0.1.0-beta",
        root_path=config.root_path,
        docs_url=None if config.is_production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = config
    application.state.providers = provider_registry
    application.state.database = database_handle

    if config.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )

    @application.middleware("http")
    async def request_safety_headers(request: Request, call_next):
        # Never accept a stable/caller-chosen trace identifier as identity.
        # Every hop gets a fresh short-lived correlation value.
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "account-api",
            "environment": config.environment,
            "provider_mode": config.auth_provider_mode,
        }

    @application.get("/ready", include_in_schema=False)
    def ready():
        try:
            database_handle.ping()
        except Exception:  # noqa: BLE001 - readiness must redact driver/URL details
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready", "providers": sorted(provider_registry), "database": "ok"}

    return application


app = create_app()
