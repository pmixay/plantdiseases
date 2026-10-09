"""Single place that wires an AccountService from validated settings.

Used by the API entry point and by operator tools (for example the deletion
replay CLI) so both construct the service identically.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.community_deletion import (
    build_community_deletion_dispatcher,
    build_community_nickname_dispatcher,
)
from app.config import Settings
from app.db.repository import AccountRepository
from app.deletion_journal import DeletionJournal
from app.providers.base import ProviderAdapter, ProviderName
from app.rate_limit import InMemoryRateLimiter
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import TokenService
from app.services.accounts import AccountService


def build_deletion_journal(config: Settings) -> DeletionJournal | None:
    if config.deletion_journal_dir is None:
        return None
    return DeletionJournal(config.deletion_journal_dir)


def build_account_service(
    config: Settings,
    *,
    database_handle,
    providers: Mapping[ProviderName, ProviderAdapter],
    internal_service_token: str | None,
) -> AccountService:
    return AccountService(
        repository=AccountRepository(database_handle.session_factory),
        providers=providers,
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
        nickname_dispatcher=build_community_nickname_dispatcher(
            config, service_token=internal_service_token
        ),
        deletion_journal=build_deletion_journal(config),
    )
