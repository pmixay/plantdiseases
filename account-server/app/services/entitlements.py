"""Server-authoritative PRO entitlements and cost-bearing feature quotas."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.errors import AccountAPIError
from app.billing.notifications import (
    RuStoreNotificationDecoder,
    SubscriptionNotification,
    TestNotification,
)
from app.billing.rustore import (
    RuStorePurchaseNotFound,
    RuStoreSubscriptionClient,
    RuStoreUnavailable,
    VerifiedSubscription,
)
from app.db.models import (
    Account,
    BillingCustomer,
    BillingNotificationReceipt,
    BillingPurchaseFetch,
    FeatureUsage,
    FeatureUsageReceipt,
    Subscription,
)
from app.db.repository import AccountRepository
from app.services.accounts import CurrentAccount

logger = logging.getLogger("plantdiseases.account.billing")

FEATURE_ORDER = (
    "gamma_analysis",
    "frontier_analysis",
    "plant_identification",
    "ai_doctor",
    "care_plan",
)
DEFAULT_FREE_LIMITS: Mapping[str, int] = {
    "gamma_analysis": 5,
    "frontier_analysis": 1,
    "plant_identification": 1,
    "ai_doctor": 3,
    "care_plan": 5,
}
DEFAULT_PRO_LIMITS: Mapping[str, int] = {
    "gamma_analysis": 30,
    "frontier_analysis": 3,
    "plant_identification": 3,
    "ai_doctor": 15,
    "care_plan": 20,
}
ACCESS_STATUSES = frozenset({"trial", "active", "grace"})
_MAX_GRACE = timedelta(days=7)
_PROVIDER_CLOCK_SKEW = timedelta(minutes=5)
_RECONCILE_AFTER = timedelta(hours=6)
_RECONCILE_RECOVERY_WINDOW = timedelta(days=7)
_RECONCILE_BATCH_SIZE = 10
_NOTIFICATION_BATCH_SIZE = 10
_NOTIFICATION_CLAIM_TTL = timedelta(minutes=5)
_POLICY_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    """Validated, operator-selected limits frozen into each decision receipt."""

    version: str
    free: Mapping[str, int]
    pro: Mapping[str, int]

    def __post_init__(self) -> None:
        expected = set(FEATURE_ORDER)
        if _POLICY_VERSION.fullmatch(self.version) is None:
            raise ValueError("invalid entitlement quota policy version")
        for name, limits in (("free", self.free), ("pro", self.pro)):
            if set(limits) != expected or any(
                type(value) is not int or not 0 <= value <= 10_000
                for value in limits.values()
            ):
                raise ValueError(f"invalid {name} entitlement quota limits")

    @classmethod
    def default(cls) -> QuotaPolicy:
        return cls(
            version="launch-2026-11-v1",
            free=dict(DEFAULT_FREE_LIMITS),
            pro=dict(DEFAULT_PRO_LIMITS),
        )

    @classmethod
    def from_settings(cls, settings) -> QuotaPolicy:
        return cls(
            version=settings.entitlement_quota_policy_version,
            free=dict(
                zip(
                    FEATURE_ORDER,
                    settings.entitlement_free_daily_limits,
                    strict=True,
                )
            ),
            pro=dict(
                zip(
                    FEATURE_ORDER,
                    settings.entitlement_pro_daily_limits,
                    strict=True,
                )
            ),
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _reset_at(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=UTC)


class EntitlementService:
    """Owns the only durable mapping from RuStore purchases to app access."""

    def __init__(
        self,
        *,
        repository: AccountRepository,
        rustore_client: RuStoreSubscriptionClient | None,
        notification_decoder: RuStoreNotificationDecoder | None,
        allowed_products: tuple[str, ...],
        allow_sandbox: bool,
        checkout_enabled: bool | None = None,
        quota_policy: QuotaPolicy | None = None,
        notification_max_attempts: int = 8,
        notification_max_age: timedelta = timedelta(days=7),
        clock=lambda: datetime.now(UTC),
    ):
        if not allowed_products or len(set(allowed_products)) != len(allowed_products):
            raise ValueError("PRO products must be a non-empty unique list")
        self.repository = repository
        self.rustore_client = rustore_client
        self.notification_decoder = notification_decoder
        self.allowed_products = allowed_products
        self.allow_sandbox = allow_sandbox
        self.checkout_enabled = (
            rustore_client is not None and notification_decoder is not None
            if checkout_enabled is None
            else bool(checkout_enabled)
        )
        self.quota_policy = quota_policy or QuotaPolicy.default()
        if not 1 <= notification_max_attempts <= 100:
            raise ValueError("notification max attempts must be between 1 and 100")
        if not timedelta(hours=1) <= notification_max_age <= timedelta(days=90):
            raise ValueError("notification max age must be between 1 hour and 90 days")
        self.notification_max_attempts = notification_max_attempts
        self.notification_max_age = notification_max_age
        self.clock = clock

    @property
    def billing_available(self) -> bool:
        """Whether checkout can be completed server-side right now."""

        return bool(
            self.checkout_enabled
            and self.rustore_client is not None
            and self.notification_decoder is not None
        )

    @property
    def lifecycle_available(self) -> bool:
        return self.rustore_client is not None and self.notification_decoder is not None

    def get_entitlements(self, current: CurrentAccount) -> dict:
        now = self.clock()
        with self.repository.transaction() as transaction:
            account = transaction.scalar(
                select(Account)
                .where(Account.id == current.account_id, Account.state == "active")
                .with_for_update()
            )
            if account is None:
                raise AccountAPIError("auth_required", status_code=401)
            customer = self._customer(transaction, account.id)
            subscriptions = list(
                transaction.scalars(
                    select(Subscription).where(
                        Subscription.billing_customer_id == customer.id
                    )
                )
            )
            return self._payload(
                transaction,
                account=account,
                customer=customer,
                subscriptions=subscriptions,
                now=now,
            )

    def verify_purchase(
        self,
        current: CurrentAccount,
        *,
        product_id: str,
        purchase_id: uuid.UUID,
        sandbox: bool,
    ) -> dict:
        self._validate_purchase_context(product_id=product_id, sandbox=sandbox)
        if not self.billing_available:
            raise AccountAPIError("billing_unavailable", status_code=503)
        fetch_generation = self._reserve_canonical_fetch(
            product_id=product_id,
            purchase_id=purchase_id,
            sandbox=sandbox,
        )
        verified = self._fetch_canonical(
            product_id=product_id,
            purchase_id=purchase_id,
            sandbox=sandbox,
        )
        now = self.clock()
        with self.repository.transaction() as transaction:
            account = transaction.scalar(
                select(Account)
                .where(Account.id == current.account_id, Account.state == "active")
                .with_for_update()
            )
            if account is None:
                raise AccountAPIError("auth_required", status_code=401)
            customer = self._customer(transaction, account.id)
            if verified.external_account_id != str(customer.external_account_id):
                raise AccountAPIError("purchase_account_mismatch", status_code=409)
            subscription = self._upsert_subscription(
                transaction,
                customer=customer,
                verified=verified,
                now=now,
                expected_generation=fetch_generation,
            )
            if subscription is None:
                raise AccountAPIError(
                    "billing_refresh_superseded",
                    status_code=409,
                )
            subscriptions = list(
                transaction.scalars(
                    select(Subscription).where(
                        Subscription.billing_customer_id == customer.id
                    )
                )
            )
            return self._payload(
                transaction,
                account=account,
                customer=customer,
                subscriptions=subscriptions,
                now=now,
            )

    def consume(
        self,
        *,
        actor_id: uuid.UUID,
        feature: str,
        request_id: uuid.UUID,
    ) -> dict:
        if feature not in self.quota_policy.free:
            raise AccountAPIError("invalid_feature", status_code=422)
        now = self.clock()
        usage_date = now.date()
        reset_at = _reset_at(now)
        with self.repository.transaction() as transaction:
            account = transaction.scalar(
                select(Account)
                .where(
                    Account.community_actor_id == actor_id,
                    Account.state == "active",
                )
                .with_for_update()
            )
            if account is None:
                raise AccountAPIError("auth_required", status_code=401)
            receipt = transaction.scalar(
                select(FeatureUsageReceipt).where(
                    FeatureUsageReceipt.account_id == account.id,
                    FeatureUsageReceipt.feature == feature,
                    FeatureUsageReceipt.request_id == request_id,
                )
            )
            if receipt is not None:
                return self._receipt_payload(receipt, replayed=True)

            customer = transaction.scalar(
                select(BillingCustomer).where(BillingCustomer.account_id == account.id)
            )
            subscriptions = (
                list(
                    transaction.scalars(
                        select(Subscription).where(
                            Subscription.billing_customer_id == customer.id
                        )
                    )
                )
                if customer is not None
                else []
            )
            plan, _status, _selected = self._access(subscriptions, now=now)
            daily_limit = (
                self.quota_policy.pro if plan == "pro" else self.quota_policy.free
            )[feature]
            usage = transaction.scalar(
                select(FeatureUsage).where(
                    FeatureUsage.account_id == account.id,
                    FeatureUsage.feature == feature,
                    FeatureUsage.usage_date == usage_date,
                )
            )
            used = usage.used if usage is not None else 0
            allowed = used < daily_limit
            if allowed:
                if usage is None:
                    usage = FeatureUsage(
                        account_id=account.id,
                        feature=feature,
                        usage_date=usage_date,
                        used=1,
                    )
                    transaction.add(usage)
                    used = 1
                else:
                    usage.used += 1
                    used = usage.used
            remaining = max(0, daily_limit - used)
            reason: Literal["pro_required", "quota_exhausted"] | None = None
            if not allowed:
                reason = "pro_required" if daily_limit == 0 else "quota_exhausted"
            receipt = FeatureUsageReceipt(
                account_id=account.id,
                feature=feature,
                request_id=request_id,
                usage_date=usage_date,
                allowed=allowed,
                plan=plan,
                policy_version=self.quota_policy.version,
                daily_limit=daily_limit,
                remaining=remaining,
                reset_at=reset_at,
                reason=reason,
            )
            transaction.add(receipt)
            transaction.flush()
            return self._receipt_payload(receipt, replayed=False)

    def release(
        self,
        *,
        actor_id: uuid.UUID,
        feature: str,
        request_id: uuid.UUID,
    ) -> dict:
        """Release one allowed reservation iff cost-bearing work never began.

        The trusted inference service is responsible for calling this only on
        the pre-upstream failure path.  The account/receipt/usage locks make a
        duplicated release harmless while preserving the immutable decision
        fields and policy version for audit.
        """

        if feature not in self.quota_policy.free:
            raise AccountAPIError("invalid_feature", status_code=422)
        now = self.clock()
        with self.repository.transaction() as transaction:
            account = transaction.scalar(
                select(Account)
                .where(
                    Account.community_actor_id == actor_id,
                    Account.state == "active",
                )
                .with_for_update()
            )
            if account is None:
                raise AccountAPIError("usage_receipt_not_found", status_code=404)
            receipt = transaction.scalar(
                select(FeatureUsageReceipt)
                .where(
                    FeatureUsageReceipt.account_id == account.id,
                    FeatureUsageReceipt.feature == feature,
                    FeatureUsageReceipt.request_id == request_id,
                )
                .with_for_update()
            )
            if receipt is None:
                raise AccountAPIError("usage_receipt_not_found", status_code=404)
            if not receipt.allowed or receipt.released_at is not None:
                return self._receipt_payload(receipt, replayed=True)
            usage = transaction.scalar(
                select(FeatureUsage)
                .where(
                    FeatureUsage.account_id == account.id,
                    FeatureUsage.feature == feature,
                    FeatureUsage.usage_date == receipt.usage_date,
                )
                .with_for_update()
            )
            if usage is None or usage.used <= 0:
                raise AccountAPIError("usage_release_conflict", status_code=409)
            usage.used -= 1
            receipt.released_at = now
            transaction.flush()
            return self._receipt_payload(receipt, replayed=True)

    def accept_notification(
        self, envelope: object
    ) -> Literal["pending", "test", "updated", "ignored", "dead_letter"]:
        """Authenticate and durably enqueue a callback without provider I/O.

        RuStore requires a literal HTTP 200 within three seconds. AES-GCM
        authentication and one short database commit stay on that request
        path; canonical V5 validation is retried by ``process_notification_jobs``.
        """

        if self.notification_decoder is None:
            raise AccountAPIError("billing_unavailable", status_code=503)
        try:
            notification = self.notification_decoder.decode(envelope)
        except ValueError:
            raise AccountAPIError("invalid_notification", status_code=400) from None
        if isinstance(notification, TestNotification):
            if notification.sandbox and not self.allow_sandbox:
                raise AccountAPIError("sandbox_forbidden", status_code=400)
            outcome: Literal["pending", "test"] = "test"
        elif isinstance(notification, SubscriptionNotification):
            self._validate_purchase_context(
                product_id=notification.product_id,
                sandbox=notification.sandbox,
            )
            outcome = "pending"
        else:
            raise AccountAPIError("invalid_notification", status_code=400)

        now = self.clock()
        try:
            with self.repository.transaction() as transaction:
                previous = self._notification_outcome(transaction, notification)
                if previous is not None:
                    return previous
                self._record_notification(
                    transaction,
                    notification=notification,
                    outcome=outcome,
                    now=now,
                )
        except IntegrityError:
            # A concurrent delivery may win the fingerprint uniqueness race.
            # Re-read and validate its authenticated fields instead of turning
            # an exact retry into a provider retry storm.
            with self.repository.transaction() as transaction:
                previous = self._notification_outcome(transaction, notification)
                if previous is None:
                    raise
                return previous
        return outcome

    def handle_notification(
        self, envelope: object
    ) -> Literal["pending", "test", "updated", "ignored", "dead_letter"]:
        """Compatibility alias for callers predating the durable inbox."""

        return self.accept_notification(envelope)

    def process_notification_jobs(
        self,
        *,
        limit: int = _NOTIFICATION_BATCH_SIZE,
    ) -> dict[str, int]:
        """Canonicalize a bounded callback batch outside request transactions."""

        if self.rustore_client is None:
            metrics = self.notification_metrics()
            if metrics["dead_letter_total"]:
                logger.error(
                    "RuStore notification dead-letter backlog",
                    extra={"dead_letter_total": metrics["dead_letter_total"]},
                )
            return {
                "claimed": 0,
                "updated": 0,
                "ignored": 0,
                "retried": 0,
                "dead_lettered": 0,
                **metrics,
            }
        bounded_limit = max(1, min(int(limit), 100))
        now = self.clock()
        claimed: list[tuple[uuid.UUID, uuid.UUID, SubscriptionNotification]] = []
        with self.repository.transaction() as transaction:
            rows = list(
                transaction.scalars(
                    select(BillingNotificationReceipt)
                    .where(
                        BillingNotificationReceipt.outcome == "pending",
                        BillingNotificationReceipt.next_attempt_at <= now,
                        or_(
                            BillingNotificationReceipt.claim_expires_at.is_(None),
                            BillingNotificationReceipt.claim_expires_at <= now,
                        ),
                    )
                    .order_by(
                        BillingNotificationReceipt.next_attempt_at,
                        BillingNotificationReceipt.created_at,
                    )
                    .limit(bounded_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for receipt in rows:
                notification = self._receipt_notification(receipt)
                claim_token = uuid.uuid4()
                receipt.claim_token = claim_token
                receipt.claim_expires_at = now + _NOTIFICATION_CLAIM_TTL
                receipt.attempt_count += 1
                receipt.updated_at = now
                claimed.append((receipt.id, claim_token, notification))

        counts = {
            "claimed": len(claimed),
            "updated": 0,
            "ignored": 0,
            "retried": 0,
            "dead_lettered": 0,
        }
        for receipt_id, claim_token, notification in claimed:
            try:
                fetch_generation = self._reserve_canonical_fetch(
                    product_id=notification.product_id,
                    purchase_id=notification.purchase_id,
                    sandbox=notification.sandbox,
                )
                verified = self._fetch_canonical(
                    product_id=notification.product_id,
                    purchase_id=notification.purchase_id,
                    sandbox=notification.sandbox,
                )
            except AccountAPIError as exc:
                outcome = self._fail_notification(
                    receipt_id=receipt_id,
                    claim_token=claim_token,
                    error_code=exc.code,
                    retryable=exc.code not in {
                        "purchase_not_found",
                        "purchase_conflict",
                        "invalid_product",
                        "sandbox_forbidden",
                    },
                )
                if outcome in {"retried", "dead_lettered"}:
                    counts[outcome] += 1
                continue
            except Exception:  # noqa: BLE001 - retain a fixed retry code only
                logger.warning("RuStore notification canonicalization failed")
                outcome = self._fail_notification(
                    receipt_id=receipt_id,
                    claim_token=claim_token,
                    error_code="billing_unavailable",
                    retryable=True,
                )
                if outcome in {"retried", "dead_lettered"}:
                    counts[outcome] += 1
                continue

            try:
                outcome = self._apply_notification_claim(
                    receipt_id=receipt_id,
                    claim_token=claim_token,
                    notification=notification,
                    verified=verified,
                    expected_generation=fetch_generation,
                )
            except Exception:  # noqa: BLE001 - durable retry isolates each event
                logger.warning("RuStore notification application failed")
                outcome = self._fail_notification(
                    receipt_id=receipt_id,
                    claim_token=claim_token,
                    error_code="billing_apply_failed",
                    retryable=True,
                )
                if outcome in {"retried", "dead_lettered"}:
                    counts[outcome] += 1
                continue
            if outcome in {"updated", "ignored", "retried", "dead_lettered"}:
                counts[outcome] += 1
        metrics = self.notification_metrics()
        counts.update(metrics)
        if metrics["dead_letter_total"]:
            logger.error(
                "RuStore notification dead-letter backlog",
                extra={"dead_letter_total": metrics["dead_letter_total"]},
            )
        return counts

    def prune_expired_usage(self) -> dict[str, int]:
        """Compact callback payloads while retaining replay fingerprints."""

        now = self.clock()
        usage_cutoff = now.date() - timedelta(days=8)
        receipt_cutoff = now - timedelta(days=8)
        fetch_cutoff = now - timedelta(days=8)
        notification_cutoff = now - timedelta(days=90)
        with self.repository.transaction() as transaction:
            terminal_notifications = list(
                transaction.scalars(
                    select(BillingNotificationReceipt)
                    .where(
                    BillingNotificationReceipt.created_at < notification_cutoff,
                    BillingNotificationReceipt.outcome.in_(
                            ("test", "updated", "ignored", "dead_letter")
                        ),
                        BillingNotificationReceipt.compacted_at.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for receipt in terminal_notifications:
                receipt.notification_id = None
                receipt.product_id = None
                receipt.purchase_id = None
                receipt.event_at = None
                receipt.event_type = None
                receipt.status_new = None
                receipt.period_new = None
                receipt.next_attempt_at = None
                receipt.claim_token = None
                receipt.claim_expires_at = None
                receipt.compacted_at = now
                receipt.updated_at = now
            receipt_result = transaction.execute(
                delete(FeatureUsageReceipt).where(
                    FeatureUsageReceipt.created_at < receipt_cutoff
                )
            )
            usage_result = transaction.execute(
                delete(FeatureUsage).where(FeatureUsage.usage_date < usage_cutoff)
            )
            fetch_result = transaction.execute(
                delete(BillingPurchaseFetch).where(
                    BillingPurchaseFetch.updated_at < fetch_cutoff,
                    ~exists(
                        select(Subscription.id).where(
                            Subscription.purchase_id
                            == BillingPurchaseFetch.purchase_id
                        )
                    ),
                )
            )
            return {
                "billing_notifications": len(terminal_notifications),
                "feature_receipts": receipt_result.rowcount or 0,
                "feature_usage": usage_result.rowcount or 0,
                "billing_fetch_guards": fetch_result.rowcount or 0,
            }

    def notification_metrics(self) -> dict[str, int]:
        """Return aggregate queue gauges suitable for logging/alert scraping."""

        with self.repository.transaction() as transaction:
            pending = transaction.scalar(
                select(func.count(BillingNotificationReceipt.id)).where(
                    BillingNotificationReceipt.outcome == "pending"
                )
            )
            dead_letter = transaction.scalar(
                select(func.count(BillingNotificationReceipt.id)).where(
                    BillingNotificationReceipt.outcome == "dead_letter"
                )
            )
        return {
            "pending_total": int(pending or 0),
            "dead_letter_total": int(dead_letter or 0),
        }

    def replay_dead_letters(self, *, limit: int = 10) -> int:
        """Explicit operator hook for replaying retained, uncompacted failures."""

        bounded_limit = max(1, min(int(limit), 100))
        now = self.clock()
        with self.repository.transaction() as transaction:
            receipts = list(
                transaction.scalars(
                    select(BillingNotificationReceipt)
                    .where(
                        BillingNotificationReceipt.outcome == "dead_letter",
                        BillingNotificationReceipt.compacted_at.is_(None),
                    )
                    .order_by(BillingNotificationReceipt.dead_lettered_at)
                    .limit(bounded_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for receipt in receipts:
                receipt.outcome = "pending"
                receipt.attempt_count = 0
                receipt.manual_replay_count += 1
                receipt.next_attempt_at = now
                receipt.claim_token = None
                receipt.claim_expires_at = None
                receipt.dead_lettered_at = None
                receipt.updated_at = now
        return len(receipts)

    def reconcile_due_subscriptions(self) -> dict[str, int]:
        """Refresh a bounded stale batch without holding DB locks over I/O.

        A missed callback must not leave a refunded or held annual purchase
        granting access until its old expiry.  Conversely, provider downtime
        must never fabricate a later expiry.  Candidates are snapshotted in a
        short transaction, fetched from canonical V5 outside the transaction,
        then applied only if no newer callback/verification won the race.
        """

        if self.rustore_client is None:
            return {
                "attempted": 0,
                "updated": 0,
                "missing": 0,
                "unavailable": 0,
                "stale": 0,
            }
        now = self.clock()
        cutoff = now - _RECONCILE_AFTER
        recovery_cutoff = now - _RECONCILE_RECOVERY_WINDOW
        with self.repository.transaction() as transaction:
            rows = list(
                transaction.execute(
                    select(
                        Subscription.id,
                        Subscription.product_id,
                        Subscription.purchase_id,
                        Subscription.sandbox,
                        Subscription.reconcile_attempted_at,
                        Subscription.billing_customer_id,
                        BillingCustomer.external_account_id,
                    )
                    .join(
                        BillingCustomer,
                        BillingCustomer.id == Subscription.billing_customer_id,
                    )
                    .where(
                        Subscription.reconcile_attempted_at <= cutoff,
                        (
                            Subscription.expires_at >= recovery_cutoff
                        )
                        | Subscription.auto_renewing.is_(True),
                    )
                    .order_by(Subscription.reconcile_attempted_at, Subscription.id)
                    .limit(_RECONCILE_BATCH_SIZE)
                )
            )

        attempted = 0
        updated = 0
        missing = 0
        unavailable = 0
        stale = 0
        for row in rows:
            if row.sandbox and not self.allow_sandbox:
                self._mark_reconcile_attempt(
                    row.id,
                    billing_customer_id=row.billing_customer_id,
                    expected_at=row.reconcile_attempted_at,
                    attempted_at=now,
                )
                continue
            attempted += 1
            try:
                fetch_generation = self._reserve_canonical_fetch(
                    product_id=row.product_id,
                    purchase_id=row.purchase_id,
                    sandbox=row.sandbox,
                )
            except AccountAPIError:
                stale += 1
                continue
            try:
                verified = self.rustore_client.validate_subscription(
                    product_id=row.product_id,
                    purchase_id=row.purchase_id,
                    sandbox=row.sandbox,
                )
            except RuStorePurchaseNotFound:
                missing += 1
                if not self._downgrade_missing_subscription(
                    row.id,
                    billing_customer_id=row.billing_customer_id,
                    purchase_id=row.purchase_id,
                    expected_generation=fetch_generation,
                    expected_at=row.reconcile_attempted_at,
                    attempted_at=now,
                ):
                    stale += 1
                continue
            except (RuStoreUnavailable, ValueError):
                unavailable += 1
                self._mark_reconcile_attempt(
                    row.id,
                    billing_customer_id=row.billing_customer_id,
                    purchase_id=row.purchase_id,
                    expected_generation=fetch_generation,
                    expected_at=row.reconcile_attempted_at,
                    attempted_at=now,
                )
                continue
            if verified.external_account_id != str(row.external_account_id):
                logger.warning(
                    "Canonical RuStore reconciliation account binding changed; ignoring"
                )
                unavailable += 1
                self._mark_reconcile_attempt(
                    row.id,
                    billing_customer_id=row.billing_customer_id,
                    purchase_id=row.purchase_id,
                    expected_generation=fetch_generation,
                    expected_at=row.reconcile_attempted_at,
                    attempted_at=now,
                )
                continue
            with self.repository.transaction() as transaction:
                # Keep the same customer -> subscription lock order used by
                # callback and app verification paths.  Reversing it here
                # creates a real PostgreSQL deadlock under a concurrent event.
                customer = transaction.scalar(
                    select(BillingCustomer)
                    .where(BillingCustomer.id == row.billing_customer_id)
                    .with_for_update()
                )
                if (
                    customer is None
                    or customer.external_account_id != row.external_account_id
                ):
                    stale += 1
                    continue
                subscription = self._upsert_subscription(
                    transaction,
                    customer=customer,
                    verified=verified,
                    now=now,
                    expected_generation=fetch_generation,
                )
                if subscription is None:
                    stale += 1
                else:
                    updated += 1
        return {
            "attempted": attempted,
            "updated": updated,
            "missing": missing,
            "unavailable": unavailable,
            "stale": stale,
        }

    def _downgrade_missing_subscription(
        self,
        subscription_id: uuid.UUID,
        *,
        billing_customer_id: uuid.UUID,
        purchase_id: uuid.UUID,
        expected_generation: int,
        expected_at: datetime,
        attempted_at: datetime,
    ) -> bool:
        """Revoke on canonical 404 while retaining the row for later recovery."""

        with self.repository.transaction() as transaction:
            customer = transaction.scalar(
                select(BillingCustomer)
                .where(BillingCustomer.id == billing_customer_id)
                .with_for_update()
            )
            if customer is None:
                return False
            fetch = transaction.scalar(
                select(BillingPurchaseFetch)
                .where(BillingPurchaseFetch.purchase_id == purchase_id)
                .with_for_update()
            )
            if fetch is None or fetch.generation != expected_generation:
                return False
            subscription = transaction.scalar(
                select(Subscription)
                .where(Subscription.id == subscription_id)
                .with_for_update()
            )
            if subscription is None or _as_utc(
                subscription.reconcile_attempted_at
            ) != _as_utc(expected_at):
                return False
            subscription.status = "expired"
            subscription.payment_state = None
            subscription.auto_renewing = False
            subscription.reconcile_attempted_at = attempted_at
            fetch.applied_generation = expected_generation
            fetch.updated_at = attempted_at
            return True

    def _mark_reconcile_attempt(
        self,
        subscription_id: uuid.UUID,
        *,
        billing_customer_id: uuid.UUID,
        purchase_id: uuid.UUID | None = None,
        expected_generation: int | None = None,
        expected_at: datetime,
        attempted_at: datetime,
    ) -> None:
        with self.repository.transaction() as transaction:
            customer = transaction.scalar(
                select(BillingCustomer)
                .where(BillingCustomer.id == billing_customer_id)
                .with_for_update()
            )
            if customer is None:
                return
            if purchase_id is not None:
                fetch = transaction.scalar(
                    select(BillingPurchaseFetch)
                    .where(BillingPurchaseFetch.purchase_id == purchase_id)
                    .with_for_update()
                )
                if fetch is None or fetch.generation != expected_generation:
                    return
            subscription = transaction.scalar(
                select(Subscription)
                .where(Subscription.id == subscription_id)
                .with_for_update()
            )
            if subscription is not None and _as_utc(
                subscription.reconcile_attempted_at
            ) == _as_utc(expected_at):
                subscription.reconcile_attempted_at = attempted_at

    @staticmethod
    def _notification_outcome(
        transaction,
        notification: TestNotification | SubscriptionNotification,
    ) -> Literal["pending", "test", "updated", "ignored", "dead_letter"] | None:
        receipt = transaction.scalar(
            select(BillingNotificationReceipt).where(
                BillingNotificationReceipt.key_epoch == notification.key_epoch,
                BillingNotificationReceipt.payload_fingerprint
                == notification.payload_fingerprint,
            )
        )
        if receipt is None:
            return None
        if receipt.compacted_at is not None:
            # The authenticated fingerprint plus key epoch remains a compact,
            # long-lived replay tombstone after bulky event fields expire.
            return receipt.outcome
        expected_kind = (
            "subscription"
            if isinstance(notification, SubscriptionNotification)
            else "test"
        )
        expected_product = (
            notification.product_id
            if isinstance(notification, SubscriptionNotification)
            else None
        )
        expected_purchase = (
            notification.purchase_id
            if isinstance(notification, SubscriptionNotification)
            else None
        )
        expected_event_at = (
            notification.event_at
            if isinstance(notification, SubscriptionNotification)
            else None
        )
        expected_event_type = (
            notification.event_type
            if isinstance(notification, SubscriptionNotification)
            else None
        )
        expected_status = (
            notification.status_new
            if isinstance(notification, SubscriptionNotification)
            else None
        )
        expected_period = (
            notification.period_new
            if isinstance(notification, SubscriptionNotification)
            else None
        )
        stored_event_at = (
            _as_utc(receipt.event_at) if receipt.event_at is not None else None
        )
        normalized_expected_event_at = (
            _as_utc(expected_event_at) if expected_event_at is not None else None
        )
        if (
            receipt.kind != expected_kind
            or receipt.sandbox != notification.sandbox
            or receipt.product_id != expected_product
            or receipt.purchase_id != expected_purchase
            or stored_event_at != normalized_expected_event_at
            or receipt.event_type != expected_event_type
            or receipt.status_new != expected_status
            or receipt.period_new != expected_period
        ):
            raise AccountAPIError("notification_conflict", status_code=409)
        return receipt.outcome

    @staticmethod
    def _record_notification(
        transaction,
        *,
        notification: TestNotification | SubscriptionNotification,
        outcome: Literal["pending", "test"],
        now: datetime,
    ) -> None:
        is_subscription = isinstance(notification, SubscriptionNotification)
        transaction.add(
            BillingNotificationReceipt(
                notification_id=notification.notification_id,
                key_epoch=notification.key_epoch,
                payload_fingerprint=notification.payload_fingerprint,
                kind="subscription" if is_subscription else "test",
                sandbox=notification.sandbox,
                product_id=notification.product_id if is_subscription else None,
                purchase_id=notification.purchase_id if is_subscription else None,
                event_at=notification.event_at if is_subscription else None,
                event_type=notification.event_type if is_subscription else None,
                status_new=notification.status_new if is_subscription else None,
                period_new=notification.period_new if is_subscription else None,
                outcome=outcome,
                attempt_count=0,
                next_attempt_at=now if is_subscription else None,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _receipt_notification(
        receipt: BillingNotificationReceipt,
    ) -> SubscriptionNotification:
        if (
            receipt.kind != "subscription"
            or receipt.notification_id is None
            or receipt.product_id is None
            or receipt.purchase_id is None
            or receipt.event_at is None
            or receipt.event_type is None
            or receipt.status_new is None
            or receipt.period_new is None
        ):
            raise RuntimeError("invalid durable billing notification")
        return SubscriptionNotification(
            notification_id=receipt.notification_id,
            payload_fingerprint=receipt.payload_fingerprint,
            sandbox=receipt.sandbox,
            product_id=receipt.product_id,
            purchase_id=receipt.purchase_id,
            event_at=_as_utc(receipt.event_at),
            event_type=receipt.event_type,
            status_new=receipt.status_new,
            period_new=receipt.period_new,
            key_epoch=receipt.key_epoch,
        )

    def _fail_notification(
        self,
        *,
        receipt_id: uuid.UUID,
        claim_token: uuid.UUID,
        error_code: str,
        retryable: bool,
    ) -> Literal["retried", "dead_lettered", "stale"]:
        now = self.clock()
        with self.repository.transaction() as transaction:
            receipt = transaction.scalar(
                select(BillingNotificationReceipt)
                .where(BillingNotificationReceipt.id == receipt_id)
                .with_for_update()
            )
            if (
                receipt is None
                or receipt.outcome != "pending"
                or receipt.claim_token != claim_token
            ):
                return "stale"
            if (
                not retryable
                or receipt.attempt_count >= self.notification_max_attempts
                or _as_utc(receipt.created_at) <= now - self.notification_max_age
            ):
                self._finish_notification_receipt(
                    receipt,
                    outcome="dead_letter",
                    now=now,
                    error_code=error_code,
                )
                return "dead_lettered"
            self._schedule_notification_retry(
                receipt,
                now=now,
                error_code=error_code,
            )
        return "retried"

    def _apply_notification_claim(
        self,
        *,
        receipt_id: uuid.UUID,
        claim_token: uuid.UUID,
        notification: SubscriptionNotification,
        verified: VerifiedSubscription,
        expected_generation: int,
    ) -> Literal["updated", "ignored", "retried", "dead_lettered", "stale"]:
        if verified.external_account_id is None:
            logger.warning("Ignoring canonical RuStore purchase without app user ID")
            return self._complete_notification_claim(
                receipt_id=receipt_id,
                claim_token=claim_token,
                outcome="ignored",
                error_code="missing_external_account_id",
            )
        try:
            external_id = uuid.UUID(verified.external_account_id)
        except ValueError:
            logger.warning("Ignoring canonical RuStore purchase with malformed app user ID")
            return self._complete_notification_claim(
                receipt_id=receipt_id,
                claim_token=claim_token,
                outcome="ignored",
                error_code="invalid_external_account_id",
            )

        now = self.clock()
        with self.repository.transaction() as transaction:
            receipt = transaction.scalar(
                select(BillingNotificationReceipt)
                .where(BillingNotificationReceipt.id == receipt_id)
                .with_for_update()
            )
            if (
                receipt is None
                or receipt.outcome != "pending"
                or receipt.claim_token != claim_token
            ):
                return "stale"
            customer = transaction.scalar(
                select(BillingCustomer)
                .where(BillingCustomer.external_account_id == external_id)
                .with_for_update()
            )
            if customer is None:
                if (
                    receipt.attempt_count >= self.notification_max_attempts
                    or _as_utc(receipt.created_at) <= now - self.notification_max_age
                ):
                    self._finish_notification_receipt(
                        receipt,
                        outcome="dead_letter",
                        now=now,
                        error_code="billing_customer_unavailable",
                    )
                    return "dead_lettered"
                self._schedule_notification_retry(
                    receipt,
                    now=now,
                    error_code="billing_customer_unavailable",
                )
                return "retried"
            try:
                subscription = self._upsert_subscription(
                    transaction,
                    customer=customer,
                    verified=verified,
                    now=now,
                    expected_generation=expected_generation,
                    notification=notification,
                )
            except AccountAPIError as exc:
                if exc.code not in {"purchase_already_claimed", "purchase_conflict"}:
                    raise
                logger.warning("Ignoring conflicting canonical RuStore purchase binding")
                self._finish_notification_receipt(
                    receipt,
                    outcome="ignored",
                    now=now,
                    error_code=exc.code,
                )
                return "ignored"
            if subscription is None:
                fetch = transaction.scalar(
                    select(BillingPurchaseFetch)
                    .where(
                        BillingPurchaseFetch.purchase_id
                        == notification.purchase_id
                    )
                    .with_for_update()
                )
                if (
                    fetch is not None
                    and fetch.applied_generation is not None
                    and fetch.applied_generation > expected_generation
                ):
                    self._finish_notification_receipt(
                        receipt,
                        outcome="ignored",
                        now=now,
                        error_code="canonical_fetch_superseded",
                    )
                    return "ignored"
                if (
                    receipt.attempt_count >= self.notification_max_attempts
                    or _as_utc(receipt.created_at)
                    <= now - self.notification_max_age
                ):
                    self._finish_notification_receipt(
                        receipt,
                        outcome="dead_letter",
                        now=now,
                        error_code="canonical_fetch_superseded",
                    )
                    return "dead_lettered"
                self._schedule_notification_retry(
                    receipt,
                    now=now,
                    error_code="canonical_fetch_superseded",
                )
                return "retried"
            self._finish_notification_receipt(
                receipt,
                outcome="updated",
                now=now,
                error_code=None,
            )
        return "updated"

    def _complete_notification_claim(
        self,
        *,
        receipt_id: uuid.UUID,
        claim_token: uuid.UUID,
        outcome: Literal["ignored"],
        error_code: str,
    ) -> Literal["ignored", "stale"]:
        now = self.clock()
        with self.repository.transaction() as transaction:
            receipt = transaction.scalar(
                select(BillingNotificationReceipt)
                .where(BillingNotificationReceipt.id == receipt_id)
                .with_for_update()
            )
            if (
                receipt is None
                or receipt.outcome != "pending"
                or receipt.claim_token != claim_token
            ):
                return "stale"
            self._finish_notification_receipt(
                receipt,
                outcome=outcome,
                now=now,
                error_code=error_code,
            )
        return outcome

    @staticmethod
    def _finish_notification_receipt(
        receipt: BillingNotificationReceipt,
        *,
        outcome: Literal["updated", "ignored", "dead_letter"],
        now: datetime,
        error_code: str | None,
    ) -> None:
        receipt.outcome = outcome
        receipt.next_attempt_at = None
        receipt.claim_token = None
        receipt.claim_expires_at = None
        receipt.last_error_code = error_code
        receipt.dead_lettered_at = now if outcome == "dead_letter" else None
        receipt.updated_at = now

    @staticmethod
    def _schedule_notification_retry(
        receipt: BillingNotificationReceipt,
        *,
        now: datetime,
        error_code: str,
    ) -> None:
        delay_seconds = min(
            86_400,
            60 * (2 ** min(max(receipt.attempt_count - 1, 0), 10)),
        )
        receipt.next_attempt_at = now + timedelta(seconds=delay_seconds)
        receipt.claim_token = None
        receipt.claim_expires_at = None
        receipt.last_error_code = error_code
        receipt.updated_at = now

    def _validate_purchase_context(self, *, product_id: str, sandbox: bool) -> None:
        if product_id not in self.allowed_products:
            raise AccountAPIError("invalid_product", status_code=400)
        if sandbox and not self.allow_sandbox:
            raise AccountAPIError("sandbox_forbidden", status_code=400)

    def _reserve_canonical_fetch(
        self,
        *,
        product_id: str,
        purchase_id: uuid.UUID,
        sandbox: bool,
    ) -> int:
        """Atomically make this fetch newer than every earlier in-flight fetch."""

        purchase_id = uuid.UUID(str(purchase_id))
        for attempt in range(2):
            try:
                with self.repository.transaction() as transaction:
                    reservation = transaction.scalar(
                        select(BillingPurchaseFetch)
                        .where(BillingPurchaseFetch.purchase_id == purchase_id)
                        .with_for_update()
                    )
                    if reservation is None:
                        reservation = BillingPurchaseFetch(
                            purchase_id=purchase_id,
                            product_id=product_id,
                            sandbox=sandbox,
                            generation=1,
                            created_at=self.clock(),
                            updated_at=self.clock(),
                        )
                        transaction.add(reservation)
                        transaction.flush()
                    else:
                        if (
                            reservation.product_id != product_id
                            or reservation.sandbox != sandbox
                        ):
                            raise AccountAPIError("purchase_conflict", status_code=409)
                        reservation.generation += 1
                        reservation.updated_at = self.clock()
                        transaction.flush()
                    return reservation.generation
            except IntegrityError:
                if attempt:
                    raise
                # A concurrent first fetch inserted the purchase guard. Retry
                # against that row and advance its generation.
                continue
        raise RuntimeError("unreachable canonical fetch reservation state")

    def _fetch_canonical(
        self,
        *,
        product_id: str,
        purchase_id: uuid.UUID,
        sandbox: bool,
    ) -> VerifiedSubscription:
        if self.rustore_client is None:
            raise AccountAPIError("billing_unavailable", status_code=503)
        try:
            return self.rustore_client.validate_subscription(
                product_id=product_id,
                purchase_id=purchase_id,
                sandbox=sandbox,
            )
        except RuStorePurchaseNotFound:
            raise AccountAPIError("purchase_not_found", status_code=404) from None
        except (RuStoreUnavailable, ValueError):
            raise AccountAPIError("billing_unavailable", status_code=503) from None

    @staticmethod
    def _customer(transaction, account_id: uuid.UUID) -> BillingCustomer:
        customer = transaction.scalar(
            select(BillingCustomer)
            .where(BillingCustomer.account_id == account_id)
            .with_for_update()
        )
        if customer is None:
            customer = BillingCustomer(
                account_id=account_id,
                external_account_id=uuid.uuid4(),
            )
            transaction.add(customer)
            transaction.flush()
        return customer

    def _upsert_subscription(
        self,
        transaction,
        *,
        customer: BillingCustomer,
        verified: VerifiedSubscription,
        now: datetime,
        expected_generation: int,
        notification: SubscriptionNotification | None = None,
    ) -> Subscription | None:
        fetch = transaction.scalar(
            select(BillingPurchaseFetch)
            .where(BillingPurchaseFetch.purchase_id == verified.purchase_id)
            .with_for_update()
        )
        if (
            fetch is None
            or fetch.product_id != verified.product_id
            or fetch.sandbox != verified.sandbox
            or fetch.generation != expected_generation
        ):
            # A newer network request was reserved after this one started.
            # Skip the entire mutation, including timestamps and event hints.
            return None
        subscription = transaction.scalar(
            select(Subscription)
            .where(Subscription.purchase_id == verified.purchase_id)
            .with_for_update()
        )
        if subscription is not None and subscription.billing_customer_id != customer.id:
            raise AccountAPIError("purchase_already_claimed", status_code=409)
        if subscription is not None and (
            subscription.product_id != verified.product_id
            or subscription.sandbox != verified.sandbox
        ):
            raise AccountAPIError("purchase_conflict", status_code=409)
        previous_status = subscription.status if subscription is not None else None
        previous_expiry = subscription.expires_at if subscription is not None else None
        previous_event_at = (
            subscription.provider_event_at if subscription is not None else None
        )
        notification_is_current = bool(
            notification is not None
            and (
                previous_event_at is None
                or _as_utc(notification.event_at) >= _as_utc(previous_event_at)
            )
        )
        notification_can_grant_grace = bool(
            notification_is_current
            and notification is not None
            and (
                previous_event_at is None
                or _as_utc(notification.event_at) > _as_utc(previous_event_at)
                or previous_status == "grace"
            )
        )
        status_value, effective_expiry = self._verified_state(
            verified,
            previous_status=previous_status,
            previous_expiry=previous_expiry,
            notification=notification if notification_is_current else None,
            notification_can_grant_grace=notification_can_grant_grace,
            now=now,
        )
        if subscription is None:
            subscription = Subscription(
                billing_customer_id=customer.id,
                product_id=verified.product_id,
                purchase_id=verified.purchase_id,
                sandbox=verified.sandbox,
                start_at=verified.start_at,
                expires_at=effective_expiry,
                auto_renewing=verified.auto_renewing,
                payment_state=verified.payment_state,
                acknowledgement_state=verified.acknowledgement_state,
                order_id=verified.order_id,
                price_currency_code=verified.price_currency_code,
                price_amount_micros=verified.price_amount_micros,
                status=status_value,
                verified_at=now,
                reconcile_attempted_at=now,
                provider_event_at=(
                    notification.event_at if notification_is_current else None
                ),
            )
            transaction.add(subscription)
        else:
            subscription.start_at = verified.start_at
            subscription.expires_at = effective_expiry
            subscription.auto_renewing = verified.auto_renewing
            subscription.payment_state = verified.payment_state
            subscription.acknowledgement_state = verified.acknowledgement_state
            subscription.order_id = verified.order_id
            subscription.price_currency_code = verified.price_currency_code
            subscription.price_amount_micros = verified.price_amount_micros
            subscription.status = status_value
            subscription.verified_at = now
            subscription.reconcile_attempted_at = now
            if notification_is_current and notification is not None:
                subscription.provider_event_at = notification.event_at
        fetch.applied_generation = expected_generation
        fetch.updated_at = now
        transaction.flush()
        return subscription

    @staticmethod
    def _verified_state(
        verified: VerifiedSubscription,
        *,
        previous_status: str | None,
        previous_expiry: datetime | None,
        notification: SubscriptionNotification | None,
        notification_can_grant_grace: bool,
        now: datetime,
    ) -> tuple[str, datetime]:
        canonical_expiry = _as_utc(verified.expires_at)
        canonical_start = _as_utc(verified.start_at)
        effective_expiry = canonical_expiry
        if canonical_expiry <= now:
            return "expired", canonical_expiry
        if canonical_start > now + _PROVIDER_CLOCK_SKEW:
            status = "pending"
        elif verified.payment_state == 2:
            status = "trial"
        elif verified.payment_state == 1:
            status = "active"
        elif verified.payment_state == 0 and previous_status == "grace":
            # A canonical refresh may preserve an already authenticated grace
            # decision, but it may never extend the seven-day cap.
            status = "grace"
            if previous_expiry is not None:
                effective_expiry = min(effective_expiry, _as_utc(previous_expiry))
        elif verified.payment_state != 2:
            status = "pending"

        if notification is not None and verified.payment_state not in {1, 2}:
            if notification.status_new in {"CLOSED", "TERMINATED"}:
                status = "expired"
            elif notification.status_new == "PAUSED" or notification.period_new == "HOLD":
                status = "pending"
            elif (
                verified.payment_state == 0
                and notification_can_grant_grace
                and canonical_start <= now + _PROVIDER_CLOCK_SKEW
                and notification.event_type == "PAYMENT_FAILED"
                and notification.status_new == "ACTIVE"
                and notification.period_new == "GRACE"
            ):
                status = "grace"
                effective_expiry = min(
                    effective_expiry,
                    notification.event_at + _MAX_GRACE,
                    now + _MAX_GRACE,
                )
        if effective_expiry <= now:
            return "expired", effective_expiry
        return status, effective_expiry

    @staticmethod
    def _effective_status(subscription: Subscription, *, now: datetime) -> str:
        if _as_utc(subscription.expires_at) <= now:
            return "expired"
        if (
            subscription.status in ACCESS_STATUSES
            and _as_utc(subscription.start_at) > now + _PROVIDER_CLOCK_SKEW
        ):
            return "pending"
        return subscription.status

    def _access(
        self,
        subscriptions: list[Subscription],
        *,
        now: datetime,
    ) -> tuple[str, str, Subscription | None]:
        eligible = [
            item for item in subscriptions if self.allow_sandbox or not item.sandbox
        ]
        if not eligible:
            return "free", "none", None
        granting = [
            item
            for item in eligible
            if self._effective_status(item, now=now) in ACCESS_STATUSES
        ]
        if granting:
            selected = max(granting, key=lambda item: _as_utc(item.expires_at))
            return "pro", self._effective_status(selected, now=now), selected
        pending = [
            item
            for item in eligible
            if self._effective_status(item, now=now) == "pending"
        ]
        if pending:
            selected = max(pending, key=lambda item: _as_utc(item.expires_at))
            return "free", "pending", selected
        selected = max(eligible, key=lambda item: _as_utc(item.expires_at))
        return "free", "expired", selected

    def _payload(
        self,
        transaction,
        *,
        account: Account,
        customer: BillingCustomer,
        subscriptions: list[Subscription],
        now: datetime,
    ) -> dict:
        plan, status_value, selected = self._access(subscriptions, now=now)
        usage_date = now.date()
        usage_by_feature = {
            row.feature: row.used
            for row in transaction.scalars(
                select(FeatureUsage).where(
                    FeatureUsage.account_id == account.id,
                    FeatureUsage.usage_date == usage_date,
                )
            )
        }
        limits = self.quota_policy.pro if plan == "pro" else self.quota_policy.free
        reset_at = _reset_at(now)
        return {
            "plan": plan,
            "status": status_value,
            "valid_until": (
                _as_utc(selected.expires_at) if selected is not None else None
            ),
            "auto_renewing": selected.auto_renewing if selected is not None else False,
            "billing_available": self.billing_available,
            "billing_external_id": customer.external_account_id,
            "products": list(self.allowed_products) if self.billing_available else [],
            "quota_policy_version": self.quota_policy.version,
            "quotas": [
                {
                    "feature": feature,
                    "daily_limit": limits[feature],
                    "remaining": max(
                        0,
                        limits[feature] - usage_by_feature.get(feature, 0),
                    ),
                    "reset_at": reset_at,
                }
                for feature in FEATURE_ORDER
            ],
        }

    @staticmethod
    def _receipt_payload(receipt: FeatureUsageReceipt, *, replayed: bool) -> dict:
        return {
            "allowed": receipt.allowed,
            "replayed": replayed,
            "plan": receipt.plan,
            "policy_version": receipt.policy_version,
            "feature": receipt.feature,
            "limit": receipt.daily_limit,
            "remaining": receipt.remaining,
            "reset_at": _as_utc(receipt.reset_at),
            "reason": receipt.reason,
            "released": receipt.released_at is not None,
        }


__all__ = [
    "ACCESS_STATUSES",
    "FEATURE_ORDER",
    "DEFAULT_FREE_LIMITS",
    "DEFAULT_PRO_LIMITS",
    "EntitlementService",
    "QuotaPolicy",
]
