"""FastAPI entry point for the independent PlantDiseases account service."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import AccountAPIError
from app.api.routes import router as account_router
from app.community_deletion import build_community_deletion_dispatcher
from app.config import Settings
from app.db.database import Database, DatabaseHandle
from app.db.repository import AccountRepository
from app.providers.base import ProviderAdapter, ProviderName
from app.providers.registry import build_provider_registry
from app.rate_limit import InMemoryRateLimiter
from app.security.internal import load_internal_service_token
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import TokenService
from app.services.accounts import AccountService

logger = logging.getLogger("plantdiseases.account")
_MAINTENANCE_INTERVAL_SECONDS = 60
_PRIVATE_PRUNE_EVERY_TICKS = 60


def create_app(
    settings: Settings | None = None,
    providers: Mapping[ProviderName, ProviderAdapter] | None = None,
    database: DatabaseHandle | None = None,
    account_service: AccountService | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    internal_service_token = load_internal_service_token(config)
    provider_registry = dict(providers) if providers is not None else build_provider_registry(config)
    if set(provider_registry) != {"yandex", "vk"}:
        raise ValueError("Both yandex and vk provider adapters are required")
    database_handle = database or Database(config.database_url)
    domain_service = account_service
    if domain_service is None and hasattr(database_handle, "session_factory"):
        domain_service = AccountService(
            repository=AccountRepository(database_handle.session_factory),
            providers=provider_registry,
            token_service=TokenService.from_settings(config),
            push_protector=PushTokenProtector.from_settings(config),
            limiter=InMemoryRateLimiter(
                requests=config.rate_limit_requests,
                window_seconds=config.rate_limit_window_seconds,
            ),
            blocked_nicknames=config.blocked_nicknames,
            deletion_dispatcher=build_community_deletion_dispatcher(
                config, service_token=internal_service_token
            ),
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        maintenance_task: asyncio.Task | None = None
        maintenance_stop = asyncio.Event()
        if domain_service is not None:
            async def maintenance_loop() -> None:
                tick = 0
                while not maintenance_stop.is_set():
                    try:
                        await asyncio.wait_for(
                            maintenance_stop.wait(),
                            timeout=_MAINTENANCE_INTERVAL_SECONDS,
                        )
                    except TimeoutError:
                        try:
                            await asyncio.to_thread(domain_service.process_deletion_jobs)
                            if tick % _PRIVATE_PRUNE_EVERY_TICKS == 0:
                                await asyncio.to_thread(
                                    domain_service.prune_expired_private_state
                                )
                            tick += 1
                        except Exception:  # noqa: BLE001 - fixed, private-safe message
                            logger.warning("Account private-state maintenance failed")

            maintenance_task = asyncio.create_task(maintenance_loop())
        try:
            yield
        finally:
            if maintenance_task is not None:
                maintenance_stop.set()
                await maintenance_task
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
    application.state.account_service = domain_service
    application.state.internal_service_token = internal_service_token

    if config.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
        if request.url.path.endswith("/.well-known/jwks.json"):
            response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.exception_handler(AccountAPIError)
    async def account_api_error(_request: Request, exc: AccountAPIError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code}},
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        # FastAPI's default validation body reflects the rejected input. Keep
        # provider, refresh, reauth, pending, and FCM values out of responses.
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request"}},
        )

    @application.exception_handler(ResponseValidationError)
    async def response_validation_error(_request: Request, _exc: ResponseValidationError):
        # The exception's repr can contain the full response, including newly
        # issued tokens. Handle it without reflecting or logging that payload.
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "service_unavailable"}},
        )

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

    application.include_router(account_router)

    return application


app = create_app()
