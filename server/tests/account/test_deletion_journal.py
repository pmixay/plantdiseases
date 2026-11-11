"""Deletion journal, backup epochs, and replay after restoring an older backup."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app import deletion_replay
from app.config import Settings
from app.db.models import Account, AccountDeletionJob, Base
from app.db.repository import AccountRepository
from app.deletion_journal import (
    BACKUP_RETENTION_DAYS,
    JOURNAL_RETENTION,
    DeletionJournal,
    JournalError,
    UnknownEpochError,
)
from app.main import create_app
from app.providers.registry import build_provider_registry
from app.rate_limit import InMemoryRateLimiter
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import SigningKeyRing, TokenService
from app.services.accounts import AccountService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


# --------------------------------------------------------------------------
# Journal file behaviour
# --------------------------------------------------------------------------


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def journal(tmp_path: Path, clock: Clock) -> DeletionJournal:
    return DeletionJournal(tmp_path / "journal", clock=clock)


def _record(journal: DeletionJournal, clock: Clock) -> tuple[uuid.UUID, uuid.UUID]:
    account_id, actor_id = uuid.uuid4(), uuid.uuid4()
    assert journal.record_deletion(
        account_id=account_id, actor_id=actor_id, requested_at=clock()
    )
    return account_id, actor_id


def test_journal_is_private_and_durable_across_instances(
    journal: DeletionJournal, clock: Clock
):
    account_id, actor_id = _record(journal, clock)

    assert stat.S_IMODE(journal.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
    again = DeletionJournal(journal.directory, clock=clock)
    [entry] = again.deletions_since(None)
    assert (entry.account_id, entry.actor_id) == (account_id, actor_id)
    # Only random internal identifiers and times are stored.
    content = journal.path.read_text()
    assert set(json.loads(content).keys()) == {
        "v", "type", "account_id", "actor_id", "requested_at", "recorded_at",
    }


def test_recording_is_idempotent_per_account(journal: DeletionJournal, clock: Clock):
    account_id, actor_id = _record(journal, clock)

    assert journal.record_deletion(
        account_id=account_id, actor_id=actor_id, requested_at=clock()
    ) is False
    assert journal.counts() == {"deletions": 1, "epochs": 0}


def test_epoch_barrier_selects_only_later_deletions(journal: DeletionJournal, clock: Clock):
    before, _ = _record(journal, clock)
    epoch = journal.begin_epoch()
    after, _ = _record(journal, clock)

    assert journal.has_epoch(epoch)
    assert [e.account_id for e in journal.deletions_since(epoch)] == [after]
    assert {e.account_id for e in journal.deletions_since(None)} == {before, after}


def test_unknown_epoch_is_refused_instead_of_guessing(journal: DeletionJournal, clock: Clock):
    _record(journal, clock)
    with pytest.raises(UnknownEpochError):
        journal.deletions_since("e20300101T000000Z-deadbeef")


def test_torn_unacknowledged_tail_is_ignored_and_discarded(
    journal: DeletionJournal, clock: Clock
):
    first, _ = _record(journal, clock)
    with open(journal.path, "ab") as handle:
        handle.write(b'{"v":1,"type":"deletion","account_id":"half-written')  # crash mid-append

    assert [e.account_id for e in journal.deletions_since(None)] == [first]
    second, _ = _record(journal, clock)  # must not inherit the torn fragment
    assert {e.account_id for e in journal.deletions_since(None)} == {first, second}
    for line in journal.path.read_text().splitlines():
        json.loads(line)


def test_mid_file_corruption_fails_closed(journal: DeletionJournal, clock: Clock):
    _record(journal, clock)
    _record(journal, clock)
    lines = journal.path.read_text().splitlines()
    journal.path.write_text(lines[0] + "\nNOT JSON\n" + lines[1] + "\n")

    with pytest.raises(JournalError):
        journal.deletions_since(None)
    with pytest.raises(JournalError):
        journal.record_deletion(
            account_id=uuid.uuid4(), actor_id=uuid.uuid4(), requested_at=clock()
        )


def test_group_writable_directory_is_rejected(tmp_path: Path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o770)
    os.chmod(directory, 0o770)
    with pytest.raises(JournalError):
        DeletionJournal(directory)


def test_prune_drops_only_entries_past_retention(journal: DeletionJournal, clock: Clock):
    old_account, _ = _record(journal, clock)
    old_epoch = journal.begin_epoch()
    clock.advance(JOURNAL_RETENTION + timedelta(days=1))
    fresh_account, _ = _record(journal, clock)

    assert journal.prune() == 2  # the old deletion and the old barrier
    assert [e.account_id for e in journal.deletions_since(None)] == [fresh_account]
    assert not journal.has_epoch(old_epoch)
    assert old_account not in {e.account_id for e in journal.deletions_since(None)}
    assert journal.prune() == 0


def test_retention_matches_policy_and_outlasts_backups():
    assert JOURNAL_RETENTION == timedelta(days=30)  # disclosed in privacy.html
    assert JOURNAL_RETENTION > timedelta(days=BACKUP_RETENTION_DAYS)
    script = (REPOSITORY_ROOT / "deploy/account-beta-test/backup-test.sh").read_text()
    pruned_after = {int(days) for days in re.findall(r"-mtime \+(\d+)", script)}
    assert pruned_after == {BACKUP_RETENTION_DAYS}, "backup pruning drifted from the journal rule"
    policy = (REPOSITORY_ROOT / "privacy.html").read_text()
    assert 'data-retention-days="30"' in policy


# --------------------------------------------------------------------------
# Service integration (SQLite) including a simulated restore of an older backup
# --------------------------------------------------------------------------


@dataclass(slots=True)
class World:
    client: TestClient
    service: AccountService
    engine: Engine
    clock: Clock
    journal: DeletionJournal
    delivered: list[uuid.UUID]
    tombstones_ok: list[bool]


class _Database:
    def __init__(self, factory: sessionmaker[OrmSession]):
        self.session_factory = factory

    def ping(self) -> None:
        return None

    def close(self) -> None:
        return None


@contextmanager
def build_world(db_engine: Engine, tmp_path: Path, clock: Clock) -> Iterator[World]:
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://beta-account-api:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
            ),
            "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
            "ACCOUNT_RATE_LIMIT_REQUESTS": "1000",
        }
    )
    factory = sessionmaker(
        bind=db_engine, class_=OrmSession, autoflush=False, expire_on_commit=False
    )
    providers = build_provider_registry(settings)
    journal = DeletionJournal(tmp_path / "journal", clock=clock)
    delivered: list[uuid.UUID] = []
    tombstones_ok: list[bool] = [True]

    def dispatcher(actor_id: uuid.UUID) -> bool:
        # The deletion must already be durable when the irreversible step runs.
        assert any(e.actor_id == actor_id for e in journal.deletions_since(None))
        delivered.append(actor_id)
        return tombstones_ok[0]

    service = AccountService(
        repository=AccountRepository(factory),
        providers=providers,
        token_service=TokenService(
            issuer=settings.issuer,
            audience=settings.audience,
            key_ring=SigningKeyRing.ephemeral(),
        ),
        push_protector=PushTokenProtector(b"p" * 32),
        limiter=InMemoryRateLimiter(requests=1000, window_seconds=60),
        clock=clock,
        deletion_dispatcher=dispatcher,
        deletion_journal=journal,
    )
    application = create_app(
        settings,
        providers=providers,
        database=_Database(factory),
        account_service=service,
    )
    with TestClient(application) as client:
        yield World(client, service, db_engine, clock, journal, delivered, tombstones_ok)


@pytest.fixture
def world(db_engine: Engine, tmp_path: Path, clock: Clock) -> Iterator[World]:
    with build_world(db_engine, tmp_path, clock) as built:
        yield built


def _signup(world: World, nickname: str) -> dict:
    installation = uuid.uuid4()
    provider_token = f"mock-yandex-generated-{uuid.uuid4().hex}"
    exchange = world.client.post(
        "/v1/auth/yandex/exchange",
        json={"provider_access_token": provider_token, "installation_id": str(installation)},
    )
    assert exchange.status_code == 200, exchange.text
    pending = exchange.json()["pending_signup_token"]
    completion = str(uuid.uuid4())
    complete = world.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending,
            "completion_id": completion,
            "nickname": nickname,
            "installation_id": str(installation),
        },
    )
    assert complete.status_code == 201, complete.text
    confirm = world.client.post(
        "/v1/auth/signup/confirm",
        json={
            "pending_signup_token": pending,
            "completion_id": completion,
            "installation_id": str(installation),
        },
    )
    assert confirm.status_code == 204, confirm.text
    return complete.json() | {"provider_token": provider_token}


def _delete_account(world: World, tokens: dict) -> None:
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    reauth = world.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": tokens["provider_token"],
            "scope": "deletion",
        },
    )
    assert reauth.status_code == 200, reauth.text
    response = world.client.request(
        "DELETE", "/v1/me", headers=headers, json={"reauth_token": reauth.json()["reauth_token"]}
    )
    assert response.status_code == 202, response.text


def _account_ids(world: World) -> set[uuid.UUID]:
    with world.service.repository.transaction() as transaction:
        return set(transaction.scalars(select(Account.id)))


def _snapshot(engine: Engine) -> dict[str, list[dict]]:
    with engine.connect() as connection:
        return {
            table.name: [dict(row._mapping) for row in connection.execute(table.select())]
            for table in Base.metadata.sorted_tables
        }


def _restore(engine: Engine, snapshot: dict[str, list[dict]]) -> None:
    """Replace every table with an older snapshot (what restoring a backup does)."""
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        for table in Base.metadata.sorted_tables:
            if snapshot[table.name]:
                connection.execute(table.insert(), snapshot[table.name])


def _replay(world: World, epoch: str | None) -> tuple[int, int]:
    entries = world.journal.deletions_since(epoch)
    queued = sum(1 for entry in entries if world.service.replay_journaled_deletion(entry))
    delivered = sum(1 for entry in entries if world.service.deliver_journaled_tombstone(entry))
    world.service.process_deletion_jobs(limit=100)
    return queued, delivered


def test_deletion_is_journaled_before_the_irreversible_steps(world: World):
    tokens = _signup(world, "JournalLeaf")
    [account_id] = _account_ids(world)
    actor_id = world.service.authenticate(tokens["access_token"]).community_actor_id

    _delete_account(world, tokens)

    [entry] = world.journal.deletions_since(None)
    assert (entry.account_id, entry.actor_id) == (account_id, actor_id)
    assert world.delivered == [actor_id]  # dispatcher's own assertion proved the order
    assert _account_ids(world) == set()


def test_journal_outage_blocks_tombstone_and_hard_delete_then_retries(
    world: World, monkeypatch
):
    tokens = _signup(world, "OutageLeaf")
    [account_id] = _account_ids(world)
    original = world.journal.record_deletion

    def unavailable(**_kwargs):
        raise JournalError("synthetic disk failure")

    monkeypatch.setattr(world.journal, "record_deletion", unavailable)
    _delete_account(world, tokens)  # still accepted (202)

    assert world.delivered == []  # nothing irreversible happened
    assert _account_ids(world) == {account_id}
    with world.service.repository.transaction() as transaction:
        job = transaction.scalar(select(AccountDeletionJob))
        assert job.state == "retry" and job.last_error_code == "journal_unavailable"

    monkeypatch.setattr(world.journal, "record_deletion", original)
    world.clock.advance(timedelta(hours=1))
    assert world.service.process_deletion_jobs(limit=10)["completed"] == 1
    assert _account_ids(world) == set()
    assert len(world.delivered) == 1


def test_restoring_an_older_backup_resurrects_nothing_after_replay(world: World):
    _restore_scenario(world)


def _restore_scenario(world: World) -> None:
    keep_tokens = _signup(world, "KeepLeaf")
    gone_tokens = _signup(world, "GoneLeaf")
    gone_id = world.service.authenticate(gone_tokens["access_token"]).account_id
    keep_id = world.service.authenticate(keep_tokens["access_token"]).account_id

    epoch = world.journal.begin_epoch()  # barrier first, then snapshot every store
    backup = _snapshot(world.engine)

    # A deletion happens after the backup was taken.
    _delete_account(world, gone_tokens)
    assert _account_ids(world) == {keep_id}

    # Disaster recovery restores the older snapshot: the deleted account is back.
    _restore(world.engine, backup)
    assert _account_ids(world) == {keep_id, gone_id}

    world.delivered.clear()
    queued, delivered = _replay(world, epoch)

    assert (queued, delivered) == (1, 1)
    assert _account_ids(world) == {keep_id}  # deleted account gone again, other untouched
    assert world.delivered.count(
        world.journal.deletions_since(epoch)[0].actor_id
    ) >= 1
    # Replay is idempotent.
    assert _replay(world, epoch)[0] == 0
    assert _account_ids(world) == {keep_id}


def test_replay_redelivers_the_tombstone_when_only_the_community_store_was_restored(
    world: World,
):
    tokens = _signup(world, "CommunityLeaf")
    actor_id = world.service.authenticate(tokens["access_token"]).community_actor_id
    epoch = world.journal.begin_epoch()
    _delete_account(world, tokens)
    assert _account_ids(world) == set()

    world.delivered.clear()  # community store was restored without the tombstone
    queued, delivered = _replay(world, epoch)

    assert (queued, delivered) == (0, 1)  # nothing to delete in accounts, tombstone re-sent
    assert world.delivered == [actor_id]


def test_replay_reports_failure_when_the_tombstone_cannot_be_delivered(world: World):
    tokens = _signup(world, "FailLeaf")
    epoch = world.journal.begin_epoch()
    _delete_account(world, tokens)

    world.tombstones_ok[0] = False
    assert world.service.deliver_journaled_tombstone(world.journal.deletions_since(epoch)[0]) is False


def test_maintenance_prunes_the_journal(world: World):
    _signup(world, "PruneLeaf")
    world.journal.begin_epoch()
    world.clock.advance(JOURNAL_RETENTION + timedelta(days=1))
    assert world.service.prune_deletion_journal() == 1
    assert world.service.prune_deletion_journal() == 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_begin_epoch_and_status(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("ACCOUNT_DELETION_JOURNAL_DIR", str(tmp_path / "cli-journal"))

    assert deletion_replay.main(["begin-epoch"]) == 0
    epoch = capsys.readouterr().out.strip()
    assert re.fullmatch(r"e\d{8}T\d{6}Z-[0-9a-f]{8}", epoch)
    assert deletion_replay.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"deletions": 0, "epochs": 1}
    assert DeletionJournal(tmp_path / "cli-journal").has_epoch(epoch)


def test_cli_requires_a_journal_directory(monkeypatch):
    monkeypatch.delenv("ACCOUNT_DELETION_JOURNAL_DIR", raising=False)
    with pytest.raises(SystemExit):
        deletion_replay.main(["status"])


def test_cli_replay_requires_epoch_or_explicit_all():
    with pytest.raises(SystemExit):
        deletion_replay.main(["replay"])



# --------------------------------------------------------------------------
# Same scenario on real PostgreSQL (opt-in, synthetic database only)
# --------------------------------------------------------------------------

POSTGRES_URL = os.environ.get("ACCOUNT_POSTGRES_INTEGRATION_URL", "")


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set ACCOUNT_POSTGRES_INTEGRATION_URL (synthetic database with CREATE SCHEMA rights)",
)
def test_postgres_restoring_an_older_backup_resurrects_nothing_after_replay(
    tmp_path: Path, clock: Clock
):
    """Run the restore scenario inside a throwaway schema; other data is untouched."""
    schema = f"pdrestore_{uuid.uuid4().hex[:12]}"
    admin = create_engine(POSTGRES_URL)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(POSTGRES_URL, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        with build_world(engine, tmp_path, clock) as world:
            _restore_scenario(world)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
