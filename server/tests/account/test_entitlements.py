from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.api.errors import AccountAPIError
from app.api.routes import RuStoreNotificationEnvelope, rustore_notification
from app.billing.notifications import SubscriptionNotification
from app.billing.rustore import (
    RuStorePurchaseNotFound,
    RuStoreUnavailable,
    VerifiedSubscription,
)
from app.db.models import (
    Account,
    BillingNotificationReceipt,
    BillingPurchaseFetch,
    FeatureUsage,
    FeatureUsageReceipt,
    Subscription,
)
from app.db.repository import AccountRepository
from app.services.accounts import CurrentAccount
from app.services.entitlements import EntitlementService, QuotaPolicy
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

MONTHLY = "plantdiseases_pro_monthly"
YEARLY = "plantdiseases_pro_yearly"


class _RuStore:
    def __init__(self):
        self.result: VerifiedSubscription | None = None
        self.calls = 0

    def validate_subscription(self, **_kwargs) -> VerifiedSubscription:
        self.calls += 1
        assert self.result is not None
        return self.result


class _Decoder:
    def __init__(self, notification: SubscriptionNotification):
        self.notification = notification

    def decode(self, _envelope):
        return self.notification


class _ConfiguredDecoder:
    def decode(self, _envelope):
        raise AssertionError("this test does not exercise callback decoding")


def _account(repository: AccountRepository, nickname: str) -> CurrentAccount:
    account_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    with repository.transaction() as transaction:
        transaction.add(
            Account(
                id=account_id,
                community_actor_id=actor_id,
                nickname=nickname,
                normalized_nickname=nickname.lower(),
                primary_provider="email",
                primary_provider_display_name="Private account",
                authorization_version=1,
                state="active",
            )
        )
    return CurrentAccount(
        account_id=account_id,
        session_id=uuid.uuid4(),
        community_actor_id=actor_id,
        nickname=nickname,
        authorization_version=1,
    )


def _service(
    db_engine,
    *,
    now: datetime,
    rustore=None,
    decoder=None,
    allow_sandbox: bool = True,
    checkout_enabled: bool | None = None,
    quota_policy: QuotaPolicy | None = None,
    notification_max_attempts: int = 8,
    notification_max_age: timedelta = timedelta(days=7),
):
    if rustore is not None and decoder is None:
        decoder = _ConfiguredDecoder()
    factory = sessionmaker(
        bind=db_engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
    repository = AccountRepository(factory)
    return repository, EntitlementService(
        repository=repository,
        rustore_client=rustore,
        notification_decoder=decoder,
        allowed_products=(MONTHLY, YEARLY),
        allow_sandbox=allow_sandbox,
        checkout_enabled=checkout_enabled,
        quota_policy=quota_policy,
        notification_max_attempts=notification_max_attempts,
        notification_max_age=notification_max_age,
        clock=lambda: now,
    )


def _verified(
    *,
    now: datetime,
    purchase_id: uuid.UUID,
    external_account_id: str | None,
    payment_state: int = 1,
    expires_at: datetime | None = None,
) -> VerifiedSubscription:
    return VerifiedSubscription(
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
        start_at=now - timedelta(days=1),
        expires_at=expires_at or now + timedelta(days=30),
        auto_renewing=True,
        payment_state=payment_state,
        acknowledgement_state="ACKNOWLEDGED",
        external_account_id=external_account_id,
        order_id="safe-provider-order",
        price_currency_code="RUB",
        price_amount_micros=199_000_000,
        country_code="RU",
    )


def test_free_entitlements_create_only_an_opaque_billing_identity(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    repository, service = _service(db_engine, now=now)
    current = _account(repository, "FreeFern")

    result = service.get_entitlements(current)

    assert result["plan"] == "free"
    assert result["status"] == "none"
    assert result["billing_available"] is False
    assert result["products"] == []
    assert isinstance(result["billing_external_id"], uuid.UUID)
    limits = {item["feature"]: item["daily_limit"] for item in result["quotas"]}
    assert limits == {
        "gamma_analysis": 15,
        "frontier_analysis": 5,
        "plant_identification": 30,
        "ai_doctor": 15,
        "care_plan": 30,
    }
    assert result["quota_policy_version"] == "launch-2026-11-v2"


def test_free_quota_is_atomic_in_shape_and_idempotent_for_retries(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    repository, service = _service(db_engine, now=now)
    current = _account(repository, "QuotaFern")
    request_id = uuid.uuid4()

    first = service.consume(
        actor_id=current.community_actor_id,
        feature="gamma_analysis",
        request_id=request_id,
    )
    retry = service.consume(
        actor_id=current.community_actor_id,
        feature="gamma_analysis",
        request_id=request_id,
    )
    for _ in range(14):
        service.consume(
            actor_id=current.community_actor_id,
            feature="gamma_analysis",
            request_id=uuid.uuid4(),
        )
    denied = service.consume(
        actor_id=current.community_actor_id,
        feature="gamma_analysis",
        request_id=uuid.uuid4(),
    )
    frontier = [
        service.consume(
            actor_id=current.community_actor_id,
            feature="frontier_analysis",
            request_id=uuid.uuid4(),
        )
        for _ in range(5)
    ]
    frontier_denied = service.consume(
        actor_id=current.community_actor_id,
        feature="frontier_analysis",
        request_id=uuid.uuid4(),
    )

    assert first["replayed"] is False
    assert retry["replayed"] is True
    assert {**first, "replayed": True} == retry
    assert first["remaining"] == 14
    assert denied["allowed"] is False
    assert denied["reason"] == "quota_exhausted"
    # Signed-in accounts get five frontier analyses a day, then the limit.
    assert all(item["allowed"] for item in frontier)
    assert frontier_denied["allowed"] is False
    assert frontier_denied["reason"] == "quota_exhausted"
    with repository.transaction() as transaction:
        assert transaction.scalar(select(func.sum(FeatureUsage.used))) == 20
        assert transaction.scalar(select(func.count(FeatureUsageReceipt.id))) == 22


def test_purchase_requires_exact_server_issued_external_account_id(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    current = _account(repository, "PaidFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )

    result = service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    assert result["plan"] == "pro"
    assert result["status"] == "active"
    assert {item["feature"]: item["daily_limit"] for item in result["quotas"]}[
        "gamma_analysis"
    ] == 30

    rustore.result = _verified(
        now=now,
        purchase_id=uuid.uuid4(),
        external_account_id=None,
    )
    with pytest.raises(AccountAPIError) as failure:
        service.verify_purchase(
            current,
            product_id=MONTHLY,
            purchase_id=rustore.result.purchase_id,
            sandbox=True,
        )
    assert failure.value.code == "purchase_account_mismatch"


def test_partial_billing_configuration_cannot_start_checkout_verification(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    current = _account(repository, "UnavailableBillingFern")
    service.notification_decoder = None
    result = service.get_entitlements(current)

    with pytest.raises(AccountAPIError) as failure:
        service.verify_purchase(
            current,
            product_id=MONTHLY,
            purchase_id=uuid.uuid4(),
            sandbox=True,
        )

    assert result["billing_available"] is False
    assert result["products"] == []
    assert failure.value.code == "billing_unavailable"
    assert rustore.calls == 0


def test_a_purchase_cannot_be_replayed_into_another_account(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    first = _account(repository, "FirstFern")
    second = _account(repository, "SecondFern")
    first_external = service.get_entitlements(first)["billing_external_id"]
    second_external = service.get_entitlements(second)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(first_external),
    )
    service.verify_purchase(
        first,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(second_external),
    )

    with pytest.raises(AccountAPIError) as failure:
        service.verify_purchase(
            second,
            product_id=MONTHLY,
            purchase_id=purchase_id,
            sandbox=True,
        )
    assert failure.value.code == "purchase_already_claimed"


def test_paid_state_moves_through_grace_and_expires_fail_closed(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    rustore = _RuStore()
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="grace-event",
        payload_fingerprint="a" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="PAYMENT_FAILED",
        status_new="ACTIVE",
        period_new="GRACE",
    )
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=_Decoder(notification),
    )
    service.clock = lambda: clock[0]
    current = _account(repository, "GraceFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    canonical_expires_at = now + timedelta(days=30)
    grace_expires_at = now + timedelta(days=7)
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=canonical_expires_at,
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
        expires_at=canonical_expires_at,
    )

    assert service.accept_notification({"encrypted": "opaque"}) == "pending"
    assert service.process_notification_jobs() == {
        "claimed": 1,
        "updated": 1,
        "ignored": 0,
        "retried": 0,
        "dead_lettered": 0,
        "pending_total": 0,
        "dead_letter_total": 0,
    }
    grace = service.get_entitlements(current)
    assert (grace["plan"], grace["status"]) == ("pro", "grace")
    assert grace["valid_until"] == grace_expires_at

    clock[0] = grace_expires_at + timedelta(seconds=1)
    expired = service.get_entitlements(current)
    assert (expired["plan"], expired["status"]) == ("free", "expired")


def test_pending_canonical_state_cannot_invent_grace_without_authenticated_hint(
    db_engine,
):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    current = _account(repository, "HoldFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
    )

    result = service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    assert (result["plan"], result["status"]) == ("free", "pending")


def test_future_subscription_period_does_not_grant_early_access(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    current = _account(repository, "FutureFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = replace(
        _verified(
            now=now,
            purchase_id=purchase_id,
            external_account_id=str(external_id),
            expires_at=now + timedelta(days=31),
        ),
        start_at=now + timedelta(days=1),
    )

    result = service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    assert (result["plan"], result["status"]) == ("free", "pending")


def test_hold_notification_revokes_access_but_grace_is_capped_at_seven_days(
    db_engine,
):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="hold-event",
        payload_fingerprint="c" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="PAYMENT_FAILED",
        status_new="PAUSED",
        period_new="HOLD",
    )
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=_Decoder(notification),
    )
    current = _account(repository, "PausedFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
        expires_at=now + timedelta(days=30),
    )

    assert service.accept_notification({"encrypted": "opaque"}) == "pending"
    assert service.process_notification_jobs()["updated"] == 1
    result = service.get_entitlements(current)

    assert (result["plan"], result["status"]) == ("free", "pending")


def test_delayed_grace_event_cannot_reopen_a_newer_hold(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    rustore = _RuStore()
    purchase_id = uuid.uuid4()
    grace_event = SubscriptionNotification(
        notification_id="grace-first",
        payload_fingerprint="d" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="PAYMENT_FAILED",
        status_new="ACTIVE",
        period_new="GRACE",
    )
    decoder = _Decoder(grace_event)
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=decoder,
    )
    service.clock = lambda: clock[0]
    current = _account(repository, "OrderedEventFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
    )
    assert service.accept_notification({"encrypted": "grace"}) == "pending"
    assert service.process_notification_jobs()["updated"] == 1

    clock[0] = now + timedelta(minutes=1)
    decoder.notification = replace(
        grace_event,
        notification_id="hold-newer",
        payload_fingerprint="e" * 64,
        event_at=clock[0],
        status_new="PAUSED",
        period_new="HOLD",
    )
    assert service.accept_notification({"encrypted": "hold"}) == "pending"
    assert service.process_notification_jobs()["updated"] == 1
    assert service.get_entitlements(current)["plan"] == "free"

    decoder.notification = replace(
        grace_event,
        notification_id="grace-delayed",
        payload_fingerprint="f" * 64,
    )
    assert service.accept_notification({"encrypted": "delayed"}) == "pending"
    assert service.process_notification_jobs()["updated"] == 1
    result = service.get_entitlements(current)

    assert (result["plan"], result["status"]) == ("free", "pending")


def test_sandbox_purchase_never_grants_when_sandbox_mode_is_disabled(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    current = _account(repository, "SandboxFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    service.allow_sandbox = False
    result = service.get_entitlements(current)

    assert (result["plan"], result["status"]) == ("free", "none")


def test_stale_subscription_is_reconciled_from_canonical_v5_in_a_bounded_batch(
    db_engine,
):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    service.clock = lambda: clock[0]
    current = _account(repository, "ReconcileFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    calls_after_verify = rustore.calls

    clock[0] = now + timedelta(hours=5)
    assert service.reconcile_due_subscriptions()["attempted"] == 0
    assert rustore.calls == calls_after_verify

    clock[0] = now + timedelta(hours=7)
    rustore.result = _verified(
        now=clock[0],
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
    )
    outcome = service.reconcile_due_subscriptions()
    result = service.get_entitlements(current)

    assert outcome == {
        "attempted": 1,
        "updated": 1,
        "missing": 0,
        "unavailable": 0,
        "stale": 0,
    }
    assert rustore.calls == calls_after_verify + 1
    assert (result["plan"], result["status"]) == ("free", "pending")


def test_reconciliation_outage_never_extends_stored_access(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    service.clock = lambda: clock[0]
    current = _account(repository, "OutageFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    original_expiry = now + timedelta(days=30)
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=original_expiry,
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    class _Unavailable:
        def validate_subscription(self, **_kwargs):
            raise RuStoreUnavailable

    service.rustore_client = _Unavailable()
    clock[0] = original_expiry + timedelta(hours=1)

    outcome = service.reconcile_due_subscriptions()
    result = service.get_entitlements(current)

    assert outcome["unavailable"] == 1
    assert (result["plan"], result["status"]) == ("free", "expired")


def test_canonical_not_found_revokes_but_a_later_canonical_result_recovers(
    db_engine,
):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    service.clock = lambda: clock[0]
    current = _account(repository, "MissingFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=now + timedelta(days=365),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    class _Missing:
        def validate_subscription(self, **_kwargs):
            raise RuStorePurchaseNotFound

    service.rustore_client = _Missing()
    clock[0] = now + timedelta(hours=7)
    missing = service.reconcile_due_subscriptions()
    revoked = service.get_entitlements(current)

    assert missing["missing"] == 1
    assert missing["unavailable"] == 0
    assert (revoked["plan"], revoked["status"]) == ("free", "expired")

    service.rustore_client = rustore
    clock[0] = now + timedelta(hours=14)
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=now + timedelta(days=365),
    )
    recovered_outcome = service.reconcile_due_subscriptions()
    recovered = service.get_entitlements(current)

    assert recovered_outcome["updated"] == 1
    assert (recovered["plan"], recovered["status"]) == ("pro", "active")


def test_notification_is_canonical_and_deduplicated_before_a_second_fetch(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="event-1",
        payload_fingerprint="b" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="ACTIVATED",
        status_new="ACTIVE",
        period_new="MAIN",
    )
    rustore = _RuStore()
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=_Decoder(notification),
    )
    current = _account(repository, "NotifyFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )

    assert service.accept_notification({"encrypted": "opaque"}) == "pending"
    assert rustore.calls == 0
    assert service.process_notification_jobs()["updated"] == 1
    # The outer ID is not authenticated. Rewriting it around the same
    # ciphertext/fingerprint must not trigger another canonical fetch.
    service.notification_decoder.notification = replace(
        notification,
        notification_id="attacker-selected-id",
    )
    assert service.accept_notification({"encrypted": "opaque"}) == "updated"
    assert rustore.calls == 1
    with repository.transaction() as transaction:
        assert transaction.scalar(select(func.count(Subscription.id))) == 1


def test_notification_provider_outage_is_durably_retried(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="retry-event",
        payload_fingerprint="9" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="ACTIVATED",
        status_new="ACTIVE",
        period_new="MAIN",
    )

    class _Unavailable:
        def validate_subscription(self, **_kwargs):
            raise RuStoreUnavailable

    repository, service = _service(
        db_engine,
        now=now,
        rustore=_Unavailable(),
        decoder=_Decoder(notification),
    )
    service.clock = lambda: clock[0]
    current = _account(repository, "RetryFern")
    external_id = service.get_entitlements(current)["billing_external_id"]

    assert service.accept_notification({"encrypted": "opaque"}) == "pending"
    assert service.process_notification_jobs()["retried"] == 1
    with repository.transaction() as transaction:
        receipt = transaction.scalar(select(BillingNotificationReceipt))
        assert receipt is not None
        assert receipt.outcome == "pending"
        assert receipt.attempt_count == 1
        assert receipt.claim_token is None
        assert receipt.next_attempt_at is not None
        assert receipt.next_attempt_at.replace(tzinfo=UTC) == now + timedelta(minutes=1)

    rustore = _RuStore()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.rustore_client = rustore
    clock[0] = now + timedelta(minutes=1, seconds=1)

    assert service.process_notification_jobs()["updated"] == 1
    assert service.get_entitlements(current)["plan"] == "pro"


def test_checkout_rollback_keeps_callbacks_and_reconciliation_active(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="hold-after-checkout-stop",
        payload_fingerprint="1" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now + timedelta(minutes=1),
        event_type="PAYMENT_FAILED",
        status_new="PAUSED",
        period_new="HOLD",
        key_epoch="staging-v1",
    )
    rustore = _RuStore()
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=_Decoder(notification),
        checkout_enabled=True,
    )
    service.clock = lambda: clock[0]
    current = _account(repository, "LifecycleFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )

    service.checkout_enabled = False
    rolled_back = service.get_entitlements(current)
    assert rolled_back["billing_available"] is False
    assert rolled_back["products"] == []
    calls_before_rejected_verify = rustore.calls
    with pytest.raises(AccountAPIError) as failure:
        service.verify_purchase(
            current,
            product_id=MONTHLY,
            purchase_id=uuid.uuid4(),
            sandbox=True,
        )
    assert failure.value.code == "billing_unavailable"
    assert rustore.calls == calls_before_rejected_verify

    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
    )
    assert service.accept_notification({"encrypted": "hold"}) == "pending"
    assert service.process_notification_jobs()["updated"] == 1
    assert service.get_entitlements(current)["status"] == "pending"

    clock[0] = now + timedelta(hours=8)
    rustore.result = _verified(
        now=clock[0],
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    assert service.reconcile_due_subscriptions()["updated"] == 1
    reconciled = service.get_entitlements(current)
    assert reconciled["plan"] == "pro"
    assert reconciled["billing_available"] is False


def test_older_verify_fetch_cannot_regress_a_newer_canonical_result(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    repository, service = _service(db_engine, now=now, rustore=_RuStore())
    current = _account(repository, "VerifyRaceFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    older = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
        expires_at=now + timedelta(days=10),
    )
    newer = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=now + timedelta(days=40),
    )

    class _NestedVerify:
        nested = False

        def validate_subscription(self, **_kwargs):
            if self.nested:
                return newer
            self.nested = True
            service.verify_purchase(
                current,
                product_id=MONTHLY,
                purchase_id=purchase_id,
                sandbox=True,
            )
            return older

    service.rustore_client = _NestedVerify()
    with pytest.raises(AccountAPIError) as failure:
        service.verify_purchase(
            current,
            product_id=MONTHLY,
            purchase_id=purchase_id,
            sandbox=True,
        )

    assert failure.value.code == "billing_refresh_superseded"
    result = service.get_entitlements(current)
    assert (result["plan"], result["status"], result["valid_until"]) == (
        "pro",
        "active",
        newer.expires_at,
    )


def test_older_callback_fetch_cannot_regress_a_newer_verification(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="callback-race",
        payload_fingerprint="2" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="PAYMENT_FAILED",
        status_new="PAUSED",
        period_new="HOLD",
    )
    repository, service = _service(
        db_engine,
        now=now,
        rustore=_RuStore(),
        decoder=_Decoder(notification),
    )
    current = _account(repository, "CallbackRaceFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    older = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
        expires_at=now + timedelta(days=10),
    )
    newer = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=now + timedelta(days=40),
    )

    class _NestedVerify:
        nested = False

        def validate_subscription(self, **_kwargs):
            if self.nested:
                return newer
            self.nested = True
            service.verify_purchase(
                current,
                product_id=MONTHLY,
                purchase_id=purchase_id,
                sandbox=True,
            )
            return older

    service.rustore_client = _NestedVerify()
    assert service.accept_notification({"encrypted": "callback"}) == "pending"

    outcome = service.process_notification_jobs()

    assert outcome["ignored"] == 1
    result = service.get_entitlements(current)
    assert (result["status"], result["valid_until"]) == ("active", newer.expires_at)
    with repository.transaction() as transaction:
        receipt = transaction.scalar(select(BillingNotificationReceipt))
        fetch = transaction.scalar(select(BillingPurchaseFetch))
        assert receipt is not None
        assert receipt.outcome == "ignored"
        assert receipt.last_error_code == "canonical_fetch_superseded"
        assert fetch is not None
        assert (fetch.generation, fetch.applied_generation) == (2, 2)


def test_superseded_callback_retries_when_newer_verification_fetch_fails(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="callback-failed-newer-race",
        payload_fingerprint="7" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="PAYMENT_FAILED",
        status_new="PAUSED",
        period_new="HOLD",
    )
    rustore = _RuStore()
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=_Decoder(notification),
    )
    service.clock = lambda: clock[0]
    current = _account(repository, "CallbackRetryRaceFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    active = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=now + timedelta(days=40),
    )
    rustore.result = active
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    hold = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
        expires_at=now + timedelta(days=10),
    )

    class _FailedNestedVerify:
        nested = False

        def validate_subscription(self, **_kwargs):
            if self.nested:
                raise RuStoreUnavailable
            self.nested = True
            with pytest.raises(AccountAPIError) as failure:
                service.verify_purchase(
                    current,
                    product_id=MONTHLY,
                    purchase_id=purchase_id,
                    sandbox=True,
                )
            assert failure.value.code == "billing_unavailable"
            return hold

    service.rustore_client = _FailedNestedVerify()
    assert service.accept_notification({"encrypted": "callback"}) == "pending"

    first = service.process_notification_jobs()

    assert first["retried"] == 1
    assert first["ignored"] == 0
    assert first["pending_total"] == 1
    result = service.get_entitlements(current)
    assert (result["plan"], result["status"], result["valid_until"]) == (
        "pro",
        "active",
        active.expires_at,
    )
    with repository.transaction() as transaction:
        receipt = transaction.scalar(select(BillingNotificationReceipt))
        fetch = transaction.scalar(select(BillingPurchaseFetch))
        assert receipt is not None
        assert receipt.outcome == "pending"
        assert receipt.last_error_code == "canonical_fetch_superseded"
        assert receipt.claim_token is None
        assert fetch is not None
        assert (fetch.generation, fetch.applied_generation) == (3, 1)

    clock[0] = now + timedelta(minutes=2)
    rustore.result = hold
    service.rustore_client = rustore
    second = service.process_notification_jobs()

    assert second["updated"] == 1
    assert second["pending_total"] == 0
    result = service.get_entitlements(current)
    assert (result["plan"], result["status"], result["valid_until"]) == (
        "free",
        "pending",
        hold.expires_at,
    )
    with repository.transaction() as transaction:
        receipt = transaction.scalar(select(BillingNotificationReceipt))
        fetch = transaction.scalar(select(BillingPurchaseFetch))
        assert receipt is not None and receipt.outcome == "updated"
        assert receipt.last_error_code is None
        assert fetch is not None
        assert (fetch.generation, fetch.applied_generation) == (4, 4)


def test_older_reconcile_fetch_cannot_regress_a_newer_verification(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    rustore = _RuStore()
    repository, service = _service(db_engine, now=now, rustore=rustore)
    service.clock = lambda: clock[0]
    current = _account(repository, "ReconcileRaceFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    purchase_id = uuid.uuid4()
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    service.verify_purchase(
        current,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        sandbox=True,
    )
    clock[0] = now + timedelta(hours=7)
    older = _verified(
        now=clock[0],
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        payment_state=0,
        expires_at=now + timedelta(days=10),
    )
    newer = _verified(
        now=clock[0],
        purchase_id=purchase_id,
        external_account_id=str(external_id),
        expires_at=now + timedelta(days=60),
    )

    class _NestedVerify:
        nested = False

        def validate_subscription(self, **_kwargs):
            if self.nested:
                return newer
            self.nested = True
            service.verify_purchase(
                current,
                product_id=MONTHLY,
                purchase_id=purchase_id,
                sandbox=True,
            )
            return older

    service.rustore_client = _NestedVerify()

    outcome = service.reconcile_due_subscriptions()

    assert outcome["stale"] == 1
    assert outcome["updated"] == 0
    result = service.get_entitlements(current)
    assert (result["status"], result["valid_until"]) == ("active", newer.expires_at)


def test_terminal_404_dead_letters_once_and_can_be_manually_replayed(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="missing-purchase",
        payload_fingerprint="3" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="ACTIVATED",
        status_new="ACTIVE",
        period_new="MAIN",
    )

    class _Missing:
        calls = 0

        def validate_subscription(self, **_kwargs):
            self.calls += 1
            raise RuStorePurchaseNotFound

    missing = _Missing()
    repository, service = _service(
        db_engine,
        now=now,
        rustore=missing,
        decoder=_Decoder(notification),
    )
    assert service.accept_notification({"encrypted": "missing"}) == "pending"

    first = service.process_notification_jobs()
    second = service.process_notification_jobs()

    assert first["dead_lettered"] == 1
    assert first["dead_letter_total"] == 1
    assert second["claimed"] == 0
    assert missing.calls == 1
    assert service.replay_dead_letters() == 1
    assert service.process_notification_jobs()["dead_lettered"] == 1
    assert missing.calls == 2
    with repository.transaction() as transaction:
        receipt = transaction.scalar(select(BillingNotificationReceipt))
        assert receipt is not None
        assert receipt.outcome == "dead_letter"
        assert receipt.manual_replay_count == 1
        assert receipt.last_error_code == "purchase_not_found"


def test_retryable_callback_is_bounded_by_attempts_and_age(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    notification = SubscriptionNotification(
        notification_id="bounded-outage",
        payload_fingerprint="4" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=uuid.uuid4(),
        event_at=now,
        event_type="ACTIVATED",
        status_new="ACTIVE",
        period_new="MAIN",
    )

    class _Unavailable:
        def validate_subscription(self, **_kwargs):
            raise RuStoreUnavailable

    _repository, service = _service(
        db_engine,
        now=now,
        rustore=_Unavailable(),
        decoder=_Decoder(notification),
        notification_max_attempts=2,
        notification_max_age=timedelta(hours=1),
    )
    service.clock = lambda: clock[0]
    assert service.accept_notification({"encrypted": "bounded"}) == "pending"
    assert service.process_notification_jobs()["retried"] == 1

    clock[0] = now + timedelta(minutes=2)
    bounded = service.process_notification_jobs()

    assert bounded["dead_lettered"] == 1
    assert bounded["pending_total"] == 0


def test_retryable_callback_is_bounded_by_age_before_attempt_limit(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    notification = SubscriptionNotification(
        notification_id="aged-outage",
        payload_fingerprint="6" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=uuid.uuid4(),
        event_at=now,
        event_type="ACTIVATED",
        status_new="ACTIVE",
        period_new="MAIN",
    )

    class _Unavailable:
        def validate_subscription(self, **_kwargs):
            raise RuStoreUnavailable

    repository, service = _service(
        db_engine,
        now=now,
        rustore=_Unavailable(),
        decoder=_Decoder(notification),
        notification_max_attempts=8,
        notification_max_age=timedelta(hours=1),
    )
    service.clock = lambda: clock[0]
    assert service.accept_notification({"encrypted": "aged"}) == "pending"
    assert service.process_notification_jobs()["retried"] == 1

    clock[0] = now + timedelta(hours=2)
    bounded = service.process_notification_jobs()

    assert bounded["dead_lettered"] == 1
    assert bounded["pending_total"] == 0
    with repository.transaction() as transaction:
        receipt = transaction.scalar(select(BillingNotificationReceipt))
        assert receipt is not None
        assert receipt.attempt_count == 2
        assert receipt.last_error_code == "billing_unavailable"


def test_a_zero_free_allowance_still_reports_pro_required(db_engine):
    """The shipped policy has no zero limits, but an operator may set one."""
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    repository, service = _service(db_engine, now=now)
    features = (
        "gamma_analysis",
        "frontier_analysis",
        "plant_identification",
        "ai_doctor",
        "care_plan",
    )
    service.quota_policy = QuotaPolicy(
        version="zero-frontier-v1",
        free={feature: (0 if feature == "frontier_analysis" else 2) for feature in features},
        pro={feature: 10 for feature in features},
    )
    current = _account(repository, "ZeroFern")

    locked = service.consume(
        actor_id=current.community_actor_id,
        feature="frontier_analysis",
        request_id=uuid.uuid4(),
    )

    assert locked["allowed"] is False
    assert locked["reason"] == "pro_required"


def test_quota_policy_replay_is_frozen_and_release_is_exactly_once(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    policy_v1 = QuotaPolicy(
        version="cohort-a-v1",
        free={feature: (1 if feature == "plant_identification" else 2) for feature in (
            "gamma_analysis",
            "frontier_analysis",
            "plant_identification",
            "ai_doctor",
            "care_plan",
        )},
        pro={feature: 10 for feature in (
            "gamma_analysis",
            "frontier_analysis",
            "plant_identification",
            "ai_doctor",
            "care_plan",
        )},
    )
    repository, service = _service(
        db_engine,
        now=now,
        quota_policy=policy_v1,
    )
    current = _account(repository, "ReleaseFern")
    request_id = uuid.uuid4()
    first = service.consume(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=request_id,
    )
    denied_id = uuid.uuid4()
    denied = service.consume(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=denied_id,
    )
    assert first["policy_version"] == "cohort-a-v1"
    assert denied["allowed"] is False

    released = service.release(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=request_id,
    )
    released_again = service.release(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=request_id,
    )
    denied_release = service.release(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=denied_id,
    )
    assert released["released"] is True
    assert released_again == released
    assert denied_release["released"] is False

    service.quota_policy = QuotaPolicy(
        version="cohort-a-v2",
        free={feature: 5 for feature in policy_v1.free},
        pro={feature: 20 for feature in policy_v1.pro},
    )
    replay = service.consume(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=request_id,
    )
    next_request = service.consume(
        actor_id=current.community_actor_id,
        feature="plant_identification",
        request_id=uuid.uuid4(),
    )
    assert replay["replayed"] is True
    assert replay["released"] is True
    assert replay["policy_version"] == "cohort-a-v1"
    assert replay["limit"] == 1
    assert next_request["policy_version"] == "cohort-a-v2"
    assert next_request["remaining"] == 4
    with repository.transaction() as transaction:
        usage = transaction.scalar(select(FeatureUsage))
        assert usage is not None and usage.used == 1

    with pytest.raises(AccountAPIError) as wrong_request:
        service.release(
            actor_id=current.community_actor_id,
            feature="plant_identification",
            request_id=uuid.uuid4(),
        )
    assert wrong_request.value.code == "usage_receipt_not_found"


def test_compaction_keeps_epoch_scoped_authenticated_replay_tombstone(db_engine):
    now = datetime(2026, 8, 26, 12, tzinfo=UTC)
    clock = [now]
    purchase_id = uuid.uuid4()
    notification = SubscriptionNotification(
        notification_id="compact-event",
        payload_fingerprint="5" * 64,
        sandbox=True,
        product_id=MONTHLY,
        purchase_id=purchase_id,
        event_at=now,
        event_type="ACTIVATED",
        status_new="ACTIVE",
        period_new="MAIN",
        key_epoch="staging-v1",
    )
    rustore = _RuStore()
    repository, service = _service(
        db_engine,
        now=now,
        rustore=rustore,
        decoder=_Decoder(notification),
    )
    service.clock = lambda: clock[0]
    current = _account(repository, "CompactFern")
    external_id = service.get_entitlements(current)["billing_external_id"]
    rustore.result = _verified(
        now=now,
        purchase_id=purchase_id,
        external_account_id=str(external_id),
    )
    assert service.accept_notification({"encrypted": "first"}) == "pending"
    assert service.process_notification_jobs()["updated"] == 1
    assert rustore.calls == 1

    clock[0] = now + timedelta(days=91)
    assert service.prune_expired_usage()["billing_notifications"] == 1
    service.notification_decoder.notification = replace(
        notification,
        notification_id="rewritten-outer-id",
    )
    assert service.accept_notification({"encrypted": "replay"}) == "updated"
    assert rustore.calls == 1
    service.notification_decoder.notification = replace(
        notification,
        notification_id="rotated-key-epoch",
        key_epoch="staging-v2",
    )
    assert service.accept_notification({"encrypted": "new-key-epoch"}) == "pending"
    with repository.transaction() as transaction:
        receipts = list(
            transaction.scalars(
                select(BillingNotificationReceipt).order_by(
                    BillingNotificationReceipt.created_at,
                    BillingNotificationReceipt.key_epoch,
                )
            )
        )
        assert len(receipts) == 2
        receipt = next(row for row in receipts if row.key_epoch == "staging-v1")
        assert receipt.compacted_at is not None
        assert receipt.notification_id is None
        assert receipt.key_epoch == "staging-v1"
        rotated = next(row for row in receipts if row.key_epoch == "staging-v2")
        assert rotated.outcome == "pending"
        assert rotated.compacted_at is None


def test_notification_route_returns_literal_200_after_durable_acceptance():
    accepted: list[object] = []

    class _AccountService:
        def limit_ip(self, _operation, _address):
            return None

    class _Entitlements:
        def accept_notification(self, envelope):
            accepted.append(envelope)
            return "pending"

    response = rustore_notification(
        RuStoreNotificationEnvelope(
            id="event",
            timestamp="2026-08-26T12:00:00Z",
            payload="opaque",
        ),
        SimpleNamespace(client=SimpleNamespace(host="203.0.113.8")),
        _AccountService(),
        _Entitlements(),
    )

    assert response.status_code == 200
    assert len(accepted) == 1


def test_notification_route_rate_limits_before_decryption_or_provider_fetch():
    calls: list[tuple[str, str]] = []

    class _AccountService:
        def limit_ip(self, operation, address):
            calls.append((operation, address))
            raise AccountAPIError("rate_limited", status_code=429)

    class _Entitlements:
        def accept_notification(self, _envelope):
            raise AssertionError("rate-limited callbacks must not reach AES-GCM")

    with pytest.raises(AccountAPIError) as failure:
        rustore_notification(
            RuStoreNotificationEnvelope(
                id="event",
                timestamp="2026-08-26T12:00:00Z",
                payload="opaque",
            ),
            SimpleNamespace(client=SimpleNamespace(host="203.0.113.8")),
            _AccountService(),
            _Entitlements(),
        )

    assert failure.value.code == "rate_limited"
    assert calls == [("billing-notification", "203.0.113.8")]
