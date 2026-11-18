"""Community roles and achievement rewards, owned by the account service.

The app API only knows community actor ids. It asks this service which roles
an actor holds before any moderation action, and reports how many
discount-bearing achievements an actor has earned. Identity data never leaves
this service: holders are listed by actor id and nickname only.

Rules enforced here, independently of the app API:

* ``admin`` is granted only by the operator CLI (``manage_roles.py``), never
  through the internal API.
* ``expert`` and ``moderator`` are granted or revoked only by an actor that
  currently holds ``admin``.
* The discount is ``min(2 % x achievements, 40 %)`` and is derived here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Account, CommunityReward, CommunityRole
from app.db.repository import AccountRepository

ROLES = ("expert", "moderator", "admin")
API_GRANTABLE_ROLES = frozenset({"expert", "moderator"})
DISCOUNT_PER_ACHIEVEMENT = 2
DISCOUNT_CAP_PERCENT = 40
MAX_ACHIEVEMENTS = 1_000
_SCOPE = re.compile(r"(\*|[a-z][a-z0-9_]{0,31})\Z")


class CommunityRoleError(ValueError):
    """A role change was refused; ``code`` is a stable error string."""

    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def discount_for(achievement_count: int) -> int:
    return min(max(0, int(achievement_count)) * DISCOUNT_PER_ACHIEVEMENT, DISCOUNT_CAP_PERCENT)


class CommunityRoleService:
    def __init__(
        self,
        *,
        repository: AccountRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    # ── lookups ────────────────────────────────────────────────────────

    @staticmethod
    def _active_account(session, actor_id: uuid.UUID) -> Account | None:
        return session.scalar(
            select(Account).where(
                Account.community_actor_id == actor_id, Account.state == "active"
            )
        )

    @staticmethod
    def _active_roles(session, account_id: uuid.UUID) -> list[CommunityRole]:
        return list(
            session.scalars(
                select(CommunityRole)
                .where(CommunityRole.account_id == account_id, CommunityRole.revoked_at.is_(None))
                .order_by(CommunityRole.role, CommunityRole.scope)
            )
        )

    def roles_for_actor(self, actor_id: uuid.UUID) -> list[dict]:
        with self._repository.transaction() as session:
            account = self._active_account(session, actor_id)
            if account is None:
                return []
            return [
                {"role": row.role, "scope": row.scope}
                for row in self._active_roles(session, account.id)
            ]

    def holders(self) -> list[dict]:
        with self._repository.transaction() as session:
            rows = session.execute(
                select(CommunityRole, Account)
                .join(Account, Account.id == CommunityRole.account_id)
                .where(CommunityRole.revoked_at.is_(None), Account.state == "active")
                .order_by(CommunityRole.role, Account.nickname)
            ).all()
            return [
                {
                    "actor_id": str(account.community_actor_id),
                    "nickname": account.nickname,
                    "role": role.role,
                    "scope": role.scope,
                    "granted_at": role.granted_at.isoformat(),
                }
                for role, account in rows
            ]

    # ── changes ────────────────────────────────────────────────────────

    @staticmethod
    def _validate(role: str, scope: str) -> None:
        if role not in ROLES:
            raise CommunityRoleError("invalid_role")
        if not _SCOPE.fullmatch(scope or ""):
            raise CommunityRoleError("invalid_scope")
        if role == "admin" and scope != "*":
            raise CommunityRoleError("invalid_scope")

    def _require_admin(self, session, actor_id: uuid.UUID) -> Account:
        account = self._active_account(session, actor_id)
        if account is None or not any(
            row.role == "admin" for row in self._active_roles(session, account.id)
        ):
            raise CommunityRoleError("admin_required", 403)
        return account

    def grant(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        scope: str = "*",
        granted_by_actor_id: uuid.UUID,
    ) -> dict:
        """An admin grants ``expert`` or ``moderator``. Idempotent."""
        self._validate(role, scope)
        if role not in API_GRANTABLE_ROLES:
            raise CommunityRoleError("role_not_grantable", 403)
        with self._repository.transaction() as session:
            granter = self._require_admin(session, granted_by_actor_id)
            target = self._active_account(session, actor_id)
            if target is None:
                raise CommunityRoleError("account_not_found", 404)
            return self._grant(session, target, role, scope, granter.id)

    def grant_by_operator(self, *, nickname: str, role: str, scope: str = "*") -> dict:
        """Operator CLI path: the only way to create an admin."""
        self._validate(role, scope)
        with self._repository.transaction() as session:
            target = session.scalar(
                select(Account).where(
                    Account.nickname == nickname, Account.state == "active"
                )
            )
            if target is None:
                raise CommunityRoleError("account_not_found", 404)
            return self._grant(session, target, role, scope, None)

    def _grant(
        self, session, target: Account, role: str, scope: str, granted_by: uuid.UUID | None
    ) -> dict:
        existing = next(
            (
                row
                for row in self._active_roles(session, target.id)
                if row.role == role and row.scope == scope
            ),
            None,
        )
        if existing is None:
            session.add(
                CommunityRole(
                    account_id=target.id,
                    role=role,
                    scope=scope,
                    granted_by_account_id=granted_by,
                    granted_at=self._clock(),
                )
            )
        return {
            "actor_id": str(target.community_actor_id),
            "role": role,
            "scope": scope,
            "created": existing is None,
        }

    def revoke(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        scope: str = "*",
        revoked_by_actor_id: uuid.UUID | None,
    ) -> bool:
        """Revoke a role. Through the API only admins revoke expert/moderator."""
        self._validate(role, scope)
        with self._repository.transaction() as session:
            if revoked_by_actor_id is not None:
                if role not in API_GRANTABLE_ROLES:
                    raise CommunityRoleError("role_not_grantable", 403)
                self._require_admin(session, revoked_by_actor_id)
            target = self._active_account(session, actor_id)
            if target is None:
                return False
            changed = False
            for row in self._active_roles(session, target.id):
                if row.role == role and row.scope == scope:
                    row.revoked_at = self._clock()
                    changed = True
            return changed

    def revoke_by_operator(self, *, nickname: str, role: str, scope: str = "*") -> bool:
        self._validate(role, scope)
        with self._repository.transaction() as session:
            target = session.scalar(select(Account).where(Account.nickname == nickname))
            if target is None:
                return False
            changed = False
            for row in self._active_roles(session, target.id):
                if row.role == role and row.scope == scope:
                    row.revoked_at = self._clock()
                    changed = True
            return changed

    # ── rewards ────────────────────────────────────────────────────────

    def set_reward(self, *, actor_id: uuid.UUID, achievement_count: int) -> dict:
        """Record the actor's current achievement count; returns the derived discount."""
        count = int(achievement_count)
        if not 0 <= count <= MAX_ACHIEVEMENTS:
            raise CommunityRoleError("invalid_achievement_count")
        with self._repository.transaction() as session:
            account = self._active_account(session, actor_id)
            if account is None:
                raise CommunityRoleError("account_not_found", 404)
            reward = session.get(CommunityReward, account.id)
            discount = discount_for(count)
            if reward is None:
                session.add(
                    CommunityReward(
                        account_id=account.id,
                        achievement_count=count,
                        discount_percent=discount,
                        updated_at=self._clock(),
                    )
                )
            else:
                reward.achievement_count = count
                reward.discount_percent = discount
                reward.updated_at = self._clock()
            return {"achievement_count": count, "discount_percent": discount}

    def reward_for_account(self, account_id: uuid.UUID) -> dict:
        with self._repository.transaction() as session:
            reward = session.get(CommunityReward, account_id)
            if reward is None:
                return {
                    "achievement_count": 0,
                    "discount_percent": 0,
                    "discount_cap_percent": DISCOUNT_CAP_PERCENT,
                    "discount_per_achievement": DISCOUNT_PER_ACHIEVEMENT,
                }
            return {
                "achievement_count": reward.achievement_count,
                "discount_percent": reward.discount_percent,
                "discount_cap_percent": DISCOUNT_CAP_PERCENT,
                "discount_per_achievement": DISCOUNT_PER_ACHIEVEMENT,
            }
