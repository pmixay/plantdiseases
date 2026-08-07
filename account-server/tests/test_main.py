from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
    with TestClient(create_app(_settings())) as client:
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
        assert ready.json() == {"status": "ready", "providers": ["vk", "yandex"]}


def test_every_response_gets_fresh_server_owned_request_id_and_safety_headers():
    with TestClient(create_app(_settings())) as client:
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


def test_both_provider_adapters_are_required():
    try:
        create_app(_settings(), providers={})
    except ValueError as exc:
        assert str(exc) == "Both yandex and vk provider adapters are required"
    else:
        raise AssertionError("create_app accepted an incomplete provider registry")


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
    with TestClient(create_app(settings)) as client:
        allowed = client.options(
            "/health",
            headers={
                "Origin": "https://allowed.invalid",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.get("/health", headers={"Origin": "https://denied.invalid"})

    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://allowed.invalid"
    assert "Access-Control-Allow-Origin" not in denied.headers
