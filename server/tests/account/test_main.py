from __future__ import annotations

from app.config import Settings
from app.main import create_app
from app.providers.registry import build_provider_registry
from fastapi.testclient import TestClient


class _FakeDatabase:
    def __init__(self, *, available: bool = True):
        self.available = available
        self.closed = False

    def ping(self) -> None:
        if not self.available:
            raise RuntimeError("postgresql://private-user:private-password@db.invalid/accounts")

    def close(self) -> None:
        self.closed = True


def _settings() -> Settings:
    return Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://beta-account-api:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
            ),
        }
    )


def test_health_and_readiness_contracts():
    database = _FakeDatabase()
    with TestClient(create_app(_settings(), database=database)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "service": "account-api",
            "environment": "test",
            "provider_mode": "mock",
        }

        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "providers": ["vk", "yandex"],
            "database": "ok",
            "push_delivery": "disabled",
        }
    assert database.closed is True


def test_every_response_gets_fresh_server_owned_request_id_and_safety_headers():
    with TestClient(create_app(_settings(), database=_FakeDatabase())) as client:
        first = client.get("/health", headers={"X-Request-ID": "caller-controlled"})
        second = client.get("/health")

    first_id = first.headers["X-Request-ID"]
    second_id = second.headers["X-Request-ID"]
    assert len(first_id) == 32
    assert first_id != "caller-controlled"
    assert second_id != first_id
    assert first.headers["Cache-Control"] == "no-store"
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert first.headers["X-Frame-Options"] == "DENY"
    assert first.headers["Referrer-Policy"] == "no-referrer"


def test_at_least_one_provider_adapter_is_required():
    try:
        create_app(_settings(), providers={}, database=_FakeDatabase())
    except ValueError as exc:
        assert str(exc) == "At least one supported provider adapter is required"
    else:
        raise AssertionError("create_app accepted an empty provider registry")


def test_one_provider_adapter_is_a_ready_service():
    yandex = build_provider_registry(_settings())["yandex"]
    with TestClient(
        create_app(_settings(), providers={"yandex": yandex}, database=_FakeDatabase())
    ) as client:
        assert client.get("/ready").json()["providers"] == ["yandex"]


def test_cors_allows_only_the_explicit_origin():
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://beta-account-api:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
            ),
            "CORS_ORIGINS": "https://allowed.invalid",
        }
    )
    with TestClient(create_app(settings, database=_FakeDatabase())) as client:
        allowed = client.options(
            "/health",
            headers={
                "Origin": "https://allowed.invalid",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.get("/health", headers={"Origin": "https://denied.invalid"})
        push_preflight = client.options(
            "/v1/push/device",
            headers={
                "Origin": "https://allowed.invalid",
                "Access-Control-Request-Method": "PUT",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://allowed.invalid"
    assert push_preflight.status_code == 200
    assert "PUT" in push_preflight.headers["Access-Control-Allow-Methods"]
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_readiness_failure_is_redacted():
    with TestClient(create_app(_settings(), database=_FakeDatabase(available=False))) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "private-password" not in response.text
