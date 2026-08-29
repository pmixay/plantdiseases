"""Atomic, deployment-wide authorization for paid vendor attempts."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.models import (
    VendorBudgetAuthority,
    VendorBudgetDaily,
    VendorBudgetReceipt,
)
from app.db.repository import AccountRepository

_PLANTNET_PROVIDER = "plantnet"
_ALLOWED_RECEIPT_RETENTION_DAYS = 90
_DAILY_LEDGER_RETENTION_DAYS = 400


class VendorBudgetUnavailable(RuntimeError):
    """The shared authority or its reviewed policy is unavailable."""


@dataclass(frozen=True, slots=True)
class PlantNetBudgetPolicy:
    version: str
    daily_attempt_limit: int
    attempt_cost_micros: int
    daily_cost_limit_micros: int

    @classmethod
    def from_settings(cls, settings: Settings) -> PlantNetBudgetPolicy | None:
        if not settings.plantnet_budget_enabled:
            return None
        # Settings.validate() already guarantees these values are complete and
        # positive. Keeping the construction explicit makes the policy version
        # and both independent ceilings visible at the domain boundary.
        assert settings.plantnet_budget_policy_version is not None
        return cls(
            version=settings.plantnet_budget_policy_version,
            daily_attempt_limit=settings.plantnet_daily_attempt_limit,
            attempt_cost_micros=settings.plantnet_attempt_cost_micros,
            daily_cost_limit_micros=settings.plantnet_daily_cost_limit_micros,
        )


@dataclass(frozen=True, slots=True)
class PlantNetBudgetDecision:
    allowed: bool
    replayed: bool
    policy_version: str
    usage_date: str
    attempt_limit: int
    attempts_used: int
    remaining_attempts: int
    attempt_cost_micros: int
    cost_limit_micros: int
    cost_exposure_micros: int
    remaining_cost_micros: int
    reason: Literal["attempt_limit", "cost_limit"] | None

    def as_dict(self) -> dict[str, bool | int | str | None]:
        return {
            "allowed": self.allowed,
            "replayed": self.replayed,
            "policy_version": self.policy_version,
            "usage_date": self.usage_date,
            "attempt_limit": self.attempt_limit,
            "attempts_used": self.attempts_used,
            "remaining_attempts": self.remaining_attempts,
            "attempt_cost_micros": self.attempt_cost_micros,
            "cost_limit_micros": self.cost_limit_micros,
            "cost_exposure_micros": self.cost_exposure_micros,
            "remaining_cost_micros": self.remaining_cost_micros,
            "reason": self.reason,
        }


class VendorBudgetService:
    """Serialize all PlantNet reservations on one permanent PostgreSQL row."""

    def __init__(
        self,
        *,
        repository: AccountRepository,
        policy: PlantNetBudgetPolicy | None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(
        cls,
        *,
        repository: AccountRepository,
        settings: Settings,
    ) -> VendorBudgetService:
        return cls(
            repository=repository,
            policy=PlantNetBudgetPolicy.from_settings(settings),
        )

    def reserve_plantnet_attempt(
        self,
        *,
        request_id: uuid.UUID,
    ) -> dict[str, bool | int | str | None]:
        policy = self.policy
        if policy is None:
            raise VendorBudgetUnavailable
        now = self.clock().astimezone(UTC)
        usage_date = now.date()
        try:
            with self.repository.transaction() as session:
                # This permanent, migration-seeded row is the contention point
                # shared by every account-api replica. The lock is held only
                # for local PostgreSQL work; no vendor network I/O occurs here.
                authority = session.scalar(
                    select(VendorBudgetAuthority)
                    .where(VendorBudgetAuthority.provider == _PLANTNET_PROVIDER)
                    .with_for_update()
                )
                if authority is None:
                    raise VendorBudgetUnavailable

                prior = session.scalar(
                    select(VendorBudgetReceipt).where(
                        VendorBudgetReceipt.authority_id == authority.id,
                        VendorBudgetReceipt.request_id == request_id,
                    )
                )
                if prior is not None:
                    return self._decision(prior, replayed=True).as_dict()

                daily = session.scalar(
                    select(VendorBudgetDaily).where(
                        VendorBudgetDaily.authority_id == authority.id,
                        VendorBudgetDaily.usage_date == usage_date,
                    )
                )
                if daily is None:
                    daily = VendorBudgetDaily(
                        authority_id=authority.id,
                        usage_date=usage_date,
                        attempts_used=0,
                        cost_exposure_micros=0,
                    )
                    session.add(daily)

                reason: Literal["attempt_limit", "cost_limit"] | None = None
                if daily.attempts_used >= policy.daily_attempt_limit:
                    reason = "attempt_limit"
                elif (
                    daily.cost_exposure_micros + policy.attempt_cost_micros
                    > policy.daily_cost_limit_micros
                ):
                    reason = "cost_limit"
                else:
                    daily.attempts_used += 1
                    daily.cost_exposure_micros += policy.attempt_cost_micros
                    daily.updated_at = now

                if reason is not None:
                    # A denial reserves no money/call. Persisting one row per
                    # random rejected UUID after exhaustion would let hostile
                    # traffic grow PostgreSQL without bound. Recompute the
                    # same fail-closed state while the ceiling remains hit.
                    return PlantNetBudgetDecision(
                        allowed=False,
                        replayed=False,
                        policy_version=policy.version,
                        usage_date=usage_date.isoformat(),
                        attempt_limit=policy.daily_attempt_limit,
                        attempts_used=daily.attempts_used,
                        remaining_attempts=max(
                            0, policy.daily_attempt_limit - daily.attempts_used
                        ),
                        attempt_cost_micros=policy.attempt_cost_micros,
                        cost_limit_micros=policy.daily_cost_limit_micros,
                        cost_exposure_micros=daily.cost_exposure_micros,
                        remaining_cost_micros=max(
                            0,
                            policy.daily_cost_limit_micros
                            - daily.cost_exposure_micros,
                        ),
                        reason=reason,
                    ).as_dict()

                receipt = VendorBudgetReceipt(
                    authority_id=authority.id,
                    request_id=request_id,
                    usage_date=usage_date,
                    allowed=reason is None,
                    policy_version=policy.version,
                    attempt_cost_micros=policy.attempt_cost_micros,
                    daily_attempt_limit=policy.daily_attempt_limit,
                    daily_cost_limit_micros=policy.daily_cost_limit_micros,
                    attempts_used=daily.attempts_used,
                    cost_exposure_micros=daily.cost_exposure_micros,
                    remaining_attempts=max(
                        0, policy.daily_attempt_limit - daily.attempts_used
                    ),
                    remaining_cost_micros=max(
                        0,
                        policy.daily_cost_limit_micros
                        - daily.cost_exposure_micros,
                    ),
                    reason=reason,
                )
                session.add(receipt)
                session.flush()
                return self._decision(receipt, replayed=False).as_dict()
        except VendorBudgetUnavailable:
            raise
        except SQLAlchemyError:
            # Database/configuration details are neither logged nor reflected.
            # The inference service treats this as a hard deny before vendor I/O.
            raise VendorBudgetUnavailable from None

    def prune_expired_state(self) -> None:
        """Bound anonymous audit receipts and historical daily aggregates."""
        now = self.clock().astimezone(UTC)
        receipt_cutoff = now - timedelta(days=_ALLOWED_RECEIPT_RETENTION_DAYS)
        ledger_cutoff = now.date() - timedelta(days=_DAILY_LEDGER_RETENTION_DAYS)
        try:
            with self.repository.transaction() as session:
                session.execute(
                    delete(VendorBudgetReceipt).where(
                        VendorBudgetReceipt.created_at <= receipt_cutoff
                    )
                )
                session.execute(
                    delete(VendorBudgetDaily).where(
                        VendorBudgetDaily.usage_date < ledger_cutoff
                    )
                )
        except SQLAlchemyError:
            raise VendorBudgetUnavailable from None

    @staticmethod
    def _decision(
        receipt: VendorBudgetReceipt,
        *,
        replayed: bool,
    ) -> PlantNetBudgetDecision:
        reason: Literal["attempt_limit", "cost_limit"] | None
        reason = receipt.reason  # type: ignore[assignment]
        return PlantNetBudgetDecision(
            allowed=receipt.allowed,
            replayed=replayed,
            policy_version=receipt.policy_version,
            usage_date=receipt.usage_date.isoformat(),
            attempt_limit=receipt.daily_attempt_limit,
            attempts_used=receipt.attempts_used,
            remaining_attempts=receipt.remaining_attempts,
            attempt_cost_micros=receipt.attempt_cost_micros,
            cost_limit_micros=receipt.daily_cost_limit_micros,
            cost_exposure_micros=receipt.cost_exposure_micros,
            remaining_cost_micros=receipt.remaining_cost_micros,
            reason=reason,
        )


__all__ = [
    "PlantNetBudgetDecision",
    "PlantNetBudgetPolicy",
    "VendorBudgetService",
    "VendorBudgetUnavailable",
]
