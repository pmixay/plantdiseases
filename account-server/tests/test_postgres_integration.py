from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, text

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, PendingSignup, RefreshToken, SignupCompletion
from app.generate_test_keys import ensure_test_keys
from app.main import create_app
from app.security.tokens import hash_opaque_token

POSTGRES_INTEGRATION_URL = os.environ.get("ACCOUNT_POSTGRES_INTEGRATION_URL", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_INTEGRATION_URL,
    reason="set ACCOUNT_POSTGRES_INTEGRATION_URL for the isolated beta PostgreSQL test",
)


def _settings(key_directory: Path) -> Settings:
    return Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://127.0.0.1:18001",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": POSTGRES_INTEGRATION_URL,
            "ACCOUNT_DATABASE_LOCAL": "true",
            "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "false",
            "ACCOUNT_SIGNING_KEY_FILE": str(key_directory / "signing.json"),
            "ACCOUNT_ENCRYPTION_KEY_FILE": str(key_directory / "encryption.key"),
            "ACCOUNT_RATE_LIMIT_REQUESTS": "1000",
        }
    )


def test_postgres_signup_rotation_and_process_restart_recovery(tmp_path: Path):
    key_directory = tmp_path / "keys"
    ensure_test_keys(key_directory, environment="test")
    settings = _settings(key_directory)
    fixture_id = uuid.uuid4().hex
    provider_token = f"mock-yandex-generated-{fixture_id}"
    nickname = f"PgLeaf{fixture_id[:12]}"
    installation_id = str(uuid.uuid4())

    first_database = Database(settings.database_url)
    with TestClient(create_app(settings, database=first_database)) as first_process:
        exchange = first_process.post(
            "/v1/auth/yandex/exchange",
            json={
                "provider_access_token": provider_token,
                "installation_id": installation_id,
            },
        )
        assert exchange.status_code == 200
        completion_id = str(uuid.uuid4())
        pending_token = exchange.json()["pending_signup_token"]
        signup = first_process.post(
            "/v1/auth/signup/complete",
            json={
                "pending_signup_token": pending_token,
                "completion_id": completion_id,
                "nickname": nickname,
                "installation_id": installation_id,
            },
        )
        assert signup.status_code == 201
        confirmed = first_process.post(
            "/v1/auth/signup/confirm",
            json={
                "pending_signup_token": pending_token,
                "completion_id": completion_id,
                "installation_id": installation_id,
            },
        )
        assert confirmed.status_code == 204
        first_jwks = first_process.get("/.well-known/jwks.json").json()
        refresh_token = signup.json()["refresh_token"]

    second_database = Database(settings.database_url)
    with TestClient(create_app(settings, database=second_database)) as restarted_process:
        assert restarted_process.get("/.well-known/jwks.json").json() == first_jwks
        recovered = restarted_process.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token, "rotation_id": str(uuid.uuid4())},
        )
        assert recovered.status_code == 200
        profile = restarted_process.get(
            "/v1/me",
            headers={"Authorization": f"Bearer {recovered.json()['access_token']}"},
        )
        assert profile.status_code == 200
        assert profile.json()["nickname"] == nickname

        with second_database.session_factory() as transaction:
            account = transaction.scalar(
                select(Account).where(Account.normalized_nickname == nickname.casefold())
            )
            rotated = transaction.scalar(
                select(RefreshToken).where(
                    RefreshToken.token_hash
                    == hash_opaque_token(recovered.json()["refresh_token"])
                )
            )
            assert account is not None
            assert rotated is not None


def test_postgres_prune_rechecks_pending_after_completion_lock_wait(tmp_path: Path):
    """A stale prune snapshot must not cascade a just-committed completion."""
    key_directory = tmp_path / "keys"
    ensure_test_keys(key_directory, environment="test")
    database = Database(_settings(key_directory).database_url)
    application = create_app(_settings(key_directory), database=database)
    fixture_id = uuid.uuid4().hex
    pending_token = f"pg-pending-{fixture_id}"
    pending_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    installation_id = uuid.uuid4()
    clock_value = [datetime.now(UTC)]

    with TestClient(application):
        service = application.state.account_service
        original_clock = service.clock
        service.clock = lambda: clock_value[0]
        with database.session_factory() as setup:
            setup.add(
                PendingSignup(
                    id=pending_id,
                    token_hash=hash_opaque_token(pending_token),
                    provider="yandex",
                    provider_subject=f"pg-prune-race-{fixture_id}",
                    provider_display_name="Postgres prune race",
                    installation_id=installation_id,
                    created_at=clock_value[0],
                    expires_at=clock_value[0] + timedelta(minutes=10),
                )
            )
            setup.commit()

        completion_inserted = threading.Event()
        release_completion = threading.Event()
        prune_query_started = threading.Event()
        prune_backend_pid: list[int] = []

        def after_cursor_execute(
            connection, _cursor, statement, _parameters, _context, _many
        ):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("insert into signup_completions"):
                completion_inserted.set()
                if not release_completion.wait(timeout=5):
                    raise TimeoutError("test did not release signup completion")

        def before_cursor_execute(
            connection, _cursor, statement, _parameters, _context, _many
        ):
            normalized = " ".join(statement.lower().split())
            if (
                "from pending_signups" in normalized
                and "pending_signups.expires_at <=" in normalized
                and "pending_signups.consumed_at is null" in normalized
            ):
                driver_connection = connection.connection.driver_connection
                prune_backend_pid.append(driver_connection.info.backend_pid)
                prune_query_started.set()

        event.listen(database.engine, "after_cursor_execute", after_cursor_execute)
        event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
        executor = ThreadPoolExecutor(max_workers=2)
        completion_future = None
        prune_future = None
        try:
            completion_future = executor.submit(
                service.complete_signup,
                pending_token=pending_token,
                completion_id=completion_id,
                nickname=f"PgRace{fixture_id[:12]}",
                installation_id=installation_id,
            )
            assert completion_inserted.wait(timeout=5)

            # Completion evaluated the still-live pending row, but pruning now
            # evaluates it as expired while the completion transaction remains
            # uncommitted and still holds the row lock.
            clock_value[0] += timedelta(minutes=11)
            prune_future = executor.submit(service.prune_expired_private_state)
            assert prune_query_started.wait(timeout=5)

            deadline = time.monotonic() + 2
            blocked_by_completion = False
            while time.monotonic() < deadline:
                with database.engine.connect() as observer:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"),
                        {"pid": prune_backend_pid[-1]},
                    )
                if blockers:
                    blocked_by_completion = True
                    break
                time.sleep(0.01)
            assert blocked_by_completion

            release_completion.set()
            completed = completion_future.result(timeout=5)
            prune_future.result(timeout=5)
            assert completed["status"] == "confirmation_required"
        finally:
            release_completion.set()
            if completion_future is not None:
                completion_future.result(timeout=5)
            if prune_future is not None:
                prune_future.result(timeout=5)
            executor.shutdown(wait=True, cancel_futures=True)
            event.remove(database.engine, "before_cursor_execute", before_cursor_execute)
            event.remove(database.engine, "after_cursor_execute", after_cursor_execute)
            service.clock = original_clock

        with database.session_factory() as verification:
            pending = verification.get(PendingSignup, pending_id)
            completion = verification.get(SignupCompletion, completion_id)
            account = verification.get(Account, completion.account_id) if completion else None
            assert pending is not None and pending.consumed_at is not None
            assert completion is not None
            assert account is not None and account.state == "signup_pending"

        # Remove this test's still-provisional resources and bounded marker.
        service.cancel_signup(
            pending_token=pending_token,
            completion_id=completion_id,
            installation_id=installation_id,
        )
        with database.session_factory() as cleanup:
            cleanup.execute(delete(PendingSignup).where(PendingSignup.id == pending_id))
            cleanup.commit()


def test_postgres_exchange_rereads_identity_after_completion_lock_wait(tmp_path: Path):
    """Exchange must not follow its stale pre-completion no-identity view."""
    key_directory = tmp_path / "keys"
    ensure_test_keys(key_directory, environment="test")
    settings = _settings(key_directory)
    database = Database(settings.database_url)
    application = create_app(settings, database=database)
    fixture_id = uuid.uuid4().hex
    provider_token = f"mock-yandex-generated-{fixture_id}"
    provider_subject = f"yandex-test-generated-{fixture_id}"
    pending_token = f"pg-exchange-race-{fixture_id}"
    pending_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    first_installation = uuid.uuid4()
    second_installation = uuid.uuid4()
    now = datetime.now(UTC)

    with TestClient(application):
        service = application.state.account_service
        with database.session_factory() as setup:
            setup.add(
                PendingSignup(
                    id=pending_id,
                    token_hash=hash_opaque_token(pending_token),
                    provider="yandex",
                    provider_subject=provider_subject,
                    provider_display_name="Postgres exchange race",
                    installation_id=first_installation,
                    created_at=now,
                    expires_at=now + timedelta(minutes=10),
                )
            )
            setup.commit()

        completion_inserted = threading.Event()
        release_completion = threading.Event()
        exchange_lock_started = threading.Event()
        exchange_backend_pid: list[int] = []

        def after_cursor_execute(
            connection, _cursor, statement, _parameters, _context, _many
        ):
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("insert into signup_completions"):
                completion_inserted.set()
                if not release_completion.wait(timeout=5):
                    raise TimeoutError("test did not release signup completion")

        def before_cursor_execute(
            connection, _cursor, statement, _parameters, _context, _many
        ):
            normalized = " ".join(statement.lower().split())
            if (
                "from pending_signups" in normalized
                and "pending_signups.provider =" in normalized
                and "pending_signups.provider_subject =" in normalized
            ):
                driver_connection = connection.connection.driver_connection
                exchange_backend_pid.append(driver_connection.info.backend_pid)
                exchange_lock_started.set()

        event.listen(database.engine, "after_cursor_execute", after_cursor_execute)
        event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
        executor = ThreadPoolExecutor(max_workers=2)
        completion_future = None
        exchange_future = None
        try:
            completion_future = executor.submit(
                service.complete_signup,
                pending_token=pending_token,
                completion_id=completion_id,
                nickname=f"PgExchange{fixture_id[:10]}",
                installation_id=first_installation,
            )
            assert completion_inserted.wait(timeout=5)
            exchange_future = executor.submit(
                lambda: asyncio.run(
                    service.exchange(
                        provider="yandex",
                        provider_access_token=provider_token,
                        installation_id=second_installation,
                    )
                )
            )
            assert exchange_lock_started.wait(timeout=5)

            deadline = time.monotonic() + 2
            blocked_by_completion = False
            while time.monotonic() < deadline:
                with database.engine.connect() as observer:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"),
                        {"pid": exchange_backend_pid[-1]},
                    )
                if blockers:
                    blocked_by_completion = True
                    break
                time.sleep(0.01)
            assert blocked_by_completion

            release_completion.set()
            completed = completion_future.result(timeout=5)
            replacement = exchange_future.result(timeout=5)
            assert completed["status"] == "confirmation_required"
            assert replacement["status"] == "profile_required"
            assert replacement["pending_signup_token"] != pending_token
        finally:
            release_completion.set()
            if completion_future is not None:
                completion_future.result(timeout=5)
            if exchange_future is not None:
                exchange_future.result(timeout=5)
            executor.shutdown(wait=True, cancel_futures=True)
            event.remove(database.engine, "before_cursor_execute", before_cursor_execute)
            event.remove(database.engine, "after_cursor_execute", after_cursor_execute)

        replacement_completion = uuid.uuid4()
        usable = service.complete_signup(
            pending_token=replacement["pending_signup_token"],
            completion_id=replacement_completion,
            nickname=f"PgReplacement{fixture_id[:8]}",
            installation_id=second_installation,
        )
        assert usable["status"] == "confirmation_required"
        with database.session_factory() as verification:
            old_marker = verification.get(SignupCompletion, completion_id)
            old_pending = verification.get(PendingSignup, pending_id)
            assert old_marker is not None and old_marker.cancelled_at is not None
            assert old_marker.account_id is None and old_marker.session_id is None
            assert old_pending is not None and old_pending.cancelled_at is not None

        service.cancel_signup(
            pending_token=replacement["pending_signup_token"],
            completion_id=replacement_completion,
            installation_id=second_installation,
        )
        with database.session_factory() as cleanup:
            cleanup.execute(
                delete(PendingSignup).where(
                    PendingSignup.provider_subject == provider_subject
                )
            )
            cleanup.commit()
