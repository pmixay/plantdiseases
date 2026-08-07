"""FastAPI entry point for the independent PlantDiseases account service."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.providers.base import ProviderAdapter, ProviderName
from app.providers.mock import build_provider_registry


def create_app(
    settings: Settings | None = None,
    providers: Mapping[ProviderName, ProviderAdapter] | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    provider_registry = dict(providers) if providers is not None else build_provider_registry(config)
    if set(provider_registry) != {"yandex", "vk"}:
        raise ValueError("Both yandex and vk provider adapters are required")

    application = FastAPI(
        title="PlantDiseases Account API",
        version="0.1.0-beta",
        root_path=config.root_path,
        docs_url=None if config.is_production else "/docs",
        redoc_url=None,
    )
    application.state.settings = config
    application.state.providers = provider_registry

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
    async def ready() -> dict[str, object]:
        return {
            "status": "ready",
            "providers": sorted(provider_registry),
        }

    return application


app = create_app()
