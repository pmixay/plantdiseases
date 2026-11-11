from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import Settings
from app.db.models import (
    VendorBudgetAuthority,
    VendorBudgetDaily,
    VendorBudgetReceipt,
)
from app.db.repository import AccountRepository
from app.main import create_app
from app.services.vendor_budgets import (
    PlantNetBudgetPolicy,
    VendorBudgetService,
    VendorBudgetUnavailable,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_AUTHORITY_ID = uuid.UUID("00000000-0000-0000-0000-000000000009")


class MutableClock:
    def __init__(self):
        self.now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class TestDatabase:
    __test__ = False

    def __init__(self, factory: sessionmaker[Session]):
        self.session_factory = factory

    def ping(self) -> None:
        return None

    def close(self) -> None:
        return None


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def _seed_authority(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        session.add(
            VendorBudgetAuthority(id=_AUTHORITY_ID, provider="plantnet")
        )


def _service(
    engine: Engine,
    *,
    clock: MutableClock | None = None,
    attempt_limit: int = 3,
    attempt_cost: int = 5_000,
    cost_limit: int = 10_000,
    version: str = "plantnet-2030-01",
) -> VendorBudgetService:
    effective_clock = clock or MutableClock()
    return VendorBudgetService(
        repository=AccountRepository(_factory(engine)),
        policy=PlantNetBudgetPolicy(
            version=version,
            daily_attempt_limit=attempt_limit,
            attempt_cost_micros=attempt_cost,
            daily_cost_limit_micros=cost_limit,
        ),
        clock=effective_clock,
    )


def _settings() -> Settings:
    return Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://beta-account-api:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/"
                "plantdisease_beta"
            ),
            "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
            "PLANTNET_BUDGET_ENABLED": "true",
            "PLANTNET_BUDGET_POLICY_VERSION": "plantnet-2030-01",
            "PLANTNET_DAILY_ATTEMPT_LIMIT": "3",
            "PLANTNET_ATTEMPT_COST_MICROS": "5000",
            "PLANTNET_DAILY_COST_LIMIT_MICROS": "10000",
        }
    )


def test_reservation_is_idempotent_and_dual_ceiling_is_shared(db_engine: Engine):
    factory = _factory(db_engine)
    _seed_authority(factory)
    service = _service(db_engine)
    first_id = uuid.uuid4()

    first = service.reserve_plantnet_attempt(request_id=first_id)
    replay = service.reserve_plantnet_attempt(request_id=first_id)
    second = service.reserve_plantnet_attempt(request_id=uuid.uuid4())
    denied = service.reserve_plantnet_attempt(request_id=uuid.uuid4())
    for _ in range(100):
        repeated_denial = service.reserve_plantnet_attempt(
            request_id=uuid.uuid4()
        )
        assert repeated_denial["allowed"] is False

    assert first == {
        "allowed": True,
        "replayed": False,
        "policy_version": "plantnet-2030-01",
        "usage_date": "2030-01-02",
        "attempt_limit": 3,
        "attempts_used": 1,
        "remaining_attempts": 2,
        "attempt_cost_micros": 5_000,
        "cost_limit_micros": 10_000,
        "cost_exposure_micros": 5_000,
        "remaining_cost_micros": 5_000,
        "reason": None,
    }
    assert replay == first | {"replayed": True}
    assert second["allowed"] is True
    assert second["attempts_used"] == 2
    assert denied["allowed"] is False
    assert denied["reason"] == "cost_limit"
    assert denied["remaining_attempts"] == 1
    assert denied["remaining_cost_micros"] == 0

    with factory() as session:
        ledger = session.scalar(select(VendorBudgetDaily))
        assert ledger is not None
        assert ledger.attempts_used == 2
        assert ledger.cost_exposure_micros == 10_000
        # Only successful reservations need idempotency/audit receipts. Random
        # traffic after exhaustion cannot create unbounded denial rows.
        assert len(session.scalars(select(VendorBudgetReceipt)).all()) == 2


def test_attempt_limit_is_independent_from_cost_limit(db_engine: Engine):
    factory = _factory(db_engine)
    _seed_authority(factory)
    service = _service(
        db_engine,
        attempt_limit=1,
        attempt_cost=1,
        cost_limit=1_000_000,
    )

    assert service.reserve_plantnet_attempt(request_id=uuid.uuid4())["allowed"]
    denied = service.reserve_plantnet_attempt(request_id=uuid.uuid4())

    assert denied["allowed"] is False
    assert denied["reason"] == "attempt_limit"
    assert denied["remaining_cost_micros"] == 999_999


def test_policy_version_change_does_not_reset_same_day_usage(db_engine: Engine):
    factory = _factory(db_engine)
    _seed_authority(factory)
    clock = MutableClock()
    initial = _service(db_engine, clock=clock, attempt_limit=1, cost_limit=5_000)
    assert initial.reserve_plantnet_attempt(request_id=uuid.uuid4())["allowed"]

    expanded = _service(
        db_engine,
        clock=clock,
        attempt_limit=2,
        cost_limit=10_000,
        version="plantnet-2030-02",
    )
    decision = expanded.reserve_plantnet_attempt(request_id=uuid.uuid4())

    assert decision["allowed"] is True
    assert decision["attempts_used"] == 2
    assert decision["cost_exposure_micros"] == 10_000
    assert decision["policy_version"] == "plantnet-2030-02"


def test_utc_day_rollover_creates_a_fresh_ledger(db_engine: Engine):
    factory = _factory(db_engine)
    _seed_authority(factory)
    clock = MutableClock()
    service = _service(db_engine, clock=clock, attempt_limit=1, cost_limit=5_000)

    first = service.reserve_plantnet_attempt(request_id=uuid.uuid4())
    clock.now += timedelta(days=1)
    second = service.reserve_plantnet_attempt(request_id=uuid.uuid4())

    assert first["usage_date"] == "2030-01-02"
    assert second["usage_date"] == "2030-01-03"
    assert second["attempts_used"] == 1
    with factory() as session:
        assert len(session.scalars(select(VendorBudgetDaily)).all()) == 2


def test_missing_policy_or_seeded_authority_fails_closed(db_engine: Engine):
    disabled = VendorBudgetService(
        repository=AccountRepository(_factory(db_engine)),
        policy=None,
    )
    with pytest.raises(VendorBudgetUnavailable):
        disabled.reserve_plantnet_attempt(request_id=uuid.uuid4())

    configured = _service(db_engine)
    with pytest.raises(VendorBudgetUnavailable):
        configured.reserve_plantnet_attempt(request_id=uuid.uuid4())


def test_allowed_receipts_are_pruned_after_reviewed_audit_window(
    db_engine: Engine,
):
    factory = _factory(db_engine)
    _seed_authority(factory)
    clock = MutableClock()
    service = _service(db_engine, clock=clock)
    service.reserve_plantnet_attempt(request_id=uuid.uuid4())
    with factory.begin() as session:
        receipt = session.scalar(select(VendorBudgetReceipt))
        assert receipt is not None
        receipt.created_at = clock.now - timedelta(days=91)

    service.prune_expired_state()

    with factory() as session:
        assert session.scalar(select(VendorBudgetReceipt)) is None


def test_internal_route_requires_service_token_and_replays(db_engine: Engine):
    factory = _factory(db_engine)
    _seed_authority(factory)
    settings = _settings()
    service = _service(db_engine)
    application = create_app(
        settings,
        database=TestDatabase(factory),
        vendor_budget_service=service,
    )
    request_id = str(uuid.uuid4())
    with TestClient(application) as client:
        unauthorized = client.post(
            "/v1/internal/vendor-budgets/plantnet/reserve",
            json={"request_id": request_id},
        )
        headers = {
            "X-Internal-Service-Token": application.state.internal_service_token
        }
        accepted = client.post(
            "/v1/internal/vendor-budgets/plantnet/reserve",
            headers=headers,
            json={"request_id": request_id},
        )
        replayed = client.post(
            "/v1/internal/vendor-budgets/plantnet/reserve",
            headers=headers,
            json={"request_id": request_id},
        )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["allowed"] is True
    assert replayed.status_code == 200
    assert replayed.json() == accepted.json() | {"replayed": True}


def test_internal_route_returns_redacted_503_when_policy_is_disabled(
    db_engine: Engine,
):
    factory = _factory(db_engine)
    settings = _settings()
    application = create_app(
        settings,
        database=TestDatabase(factory),
        vendor_budget_service=VendorBudgetService(
            repository=AccountRepository(factory),
            policy=None,
        ),
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/internal/vendor-budgets/plantnet/reserve",
            headers={
                "X-Internal-Service-Token": application.state.internal_service_token
            },
            json={"request_id": str(uuid.uuid4())},
        )

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "vendor_budget_unavailable"}}
