"""Environment configuration with fail-closed production safety checks."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, urlparse


class SettingsError(ValueError):
    """Raised when account-api configuration is missing or unsafe."""


_REQUIRED_SETTINGS = (
    "ENVIRONMENT",
    "AUTH_PROVIDER_MODE",
    "ACCOUNT_ISSUER",
    "ACCOUNT_AUDIENCE",
    "ACCOUNT_DATABASE_URL",
)
_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})
_PROVIDER_MODES = frozenset({"mock", "remote"})
_TLS_DATABASE_MODES = frozenset({"require", "verify-ca", "verify-full"})
_LOOPBACK_DATABASE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_INTERNAL_SERVICE_HOST = re.compile(
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


def _is_internal_service_host(hostname: str) -> bool:
    # Reject numeric/legacy IP spellings (for example a single decimal IPv4
    # value) even though a resolver may accept them as one DNS label.
    return bool(_INTERNAL_SERVICE_HOST.fullmatch(hostname))


def _required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name, "").strip()
    if not value:
        raise SettingsError(f"Missing required setting: {name}")
    return value


def _optional_path(source: Mapping[str, str], name: str) -> Path | None:
    value = source.get(name, "").strip()
    return Path(value) if value else None


def _csv(source: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in source.get(name, "").split(",") if part.strip())


def _boolean(source: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    value = source.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be a boolean")


def _bounded_int(
    source: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = source.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated account-api settings.

    There are intentionally no implicit service defaults. A missing
    ``ENVIRONMENT`` must not turn an accidentally exposed container into a
    mock-auth server, and a missing database URL must not fall back to a local
    SQLite file outside the beta volumes.
    """

    environment: str
    auth_provider_mode: str
    issuer: str
    audience: str
    database_url: str
    root_path: str = ""
    cors_origins: tuple[str, ...] = ()
    signing_key_file: Path | None = None
    encryption_key_file: Path | None = None
    internal_service_token_file: Path | None = None
    database_is_local: bool = False
    allow_ephemeral_keys: bool = False
    blocked_nicknames: tuple[str, ...] = ()
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    community_deletion_url: str | None = None
    yandex_client_id: str | None = None
    vk_client_id: str | None = None
    provider_http_timeout_seconds: int = 5

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        missing = [name for name in _REQUIRED_SETTINGS if not source.get(name, "").strip()]
        if missing:
            raise SettingsError(f"Missing required settings: {', '.join(missing)}")

        root_path = source.get("ACCOUNT_ROOT_PATH", "").strip()
        if root_path:
            root_path = f"/{root_path.strip('/')}"

        settings = cls(
            environment=_required(source, "ENVIRONMENT").lower(),
            auth_provider_mode=_required(source, "AUTH_PROVIDER_MODE").lower(),
            issuer=_required(source, "ACCOUNT_ISSUER").rstrip("/"),
            audience=_required(source, "ACCOUNT_AUDIENCE"),
            database_url=_required(source, "ACCOUNT_DATABASE_URL"),
            root_path=root_path,
            cors_origins=_csv(source, "CORS_ORIGINS"),
            signing_key_file=_optional_path(source, "ACCOUNT_SIGNING_KEY_FILE"),
            encryption_key_file=_optional_path(source, "ACCOUNT_ENCRYPTION_KEY_FILE"),
            internal_service_token_file=_optional_path(source, "INTERNAL_SERVICE_TOKEN_FILE"),
            database_is_local=_boolean(source, "ACCOUNT_DATABASE_LOCAL"),
            allow_ephemeral_keys=_boolean(source, "ACCOUNT_ALLOW_EPHEMERAL_KEYS"),
            blocked_nicknames=_csv(source, "ACCOUNT_BLOCKED_NICKNAMES"),
            rate_limit_requests=_bounded_int(
                source,
                "ACCOUNT_RATE_LIMIT_REQUESTS",
                default=60,
                minimum=1,
                maximum=10_000,
            ),
            rate_limit_window_seconds=_bounded_int(
                source,
                "ACCOUNT_RATE_LIMIT_WINDOW_SECONDS",
                default=60,
                minimum=1,
                maximum=3_600,
            ),
            community_deletion_url=(
                source.get("COMMUNITY_DELETION_URL", "").strip() or None
            ),
            yandex_client_id=source.get("YANDEX_CLIENT_ID", "").strip() or None,
            vk_client_id=source.get("VKID_CLIENT_ID", "").strip() or None,
            provider_http_timeout_seconds=_bounded_int(
                source,
                "PROVIDER_HTTP_TIMEOUT_SECONDS",
                default=5,
                minimum=1,
                maximum=15,
            ),
        )
        settings.validate()
        return settings

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.environment not in _ENVIRONMENTS:
            raise SettingsError(f"Unsupported ENVIRONMENT: {self.environment}")
        if self.auth_provider_mode not in _PROVIDER_MODES:
            raise SettingsError(f"Unsupported AUTH_PROVIDER_MODE: {self.auth_provider_mode}")
        if self.auth_provider_mode == "remote":
            if not self.yandex_client_id and not self.vk_client_id:
                raise SettingsError(
                    "Remote provider mode requires YANDEX_CLIENT_ID or VKID_CLIENT_ID"
                )
            if self.vk_client_id is not None and not self.vk_client_id.isdecimal():
                raise SettingsError("VKID_CLIENT_ID must be a decimal application ID")

        issuer = urlparse(self.issuer)
        if issuer.scheme not in {"http", "https"} or not issuer.hostname:
            raise SettingsError("ACCOUNT_ISSUER must be an absolute HTTP(S) URL")
        if issuer.username or issuer.password or issuer.query or issuer.fragment:
            raise SettingsError("ACCOUNT_ISSUER must not contain credentials, query, or fragment")

        database = urlparse(self.database_url)
        if database.scheme != "postgresql+psycopg":
            raise SettingsError("ACCOUNT_DATABASE_URL must use postgresql+psycopg")
        if not database.hostname or not database.path.strip("/"):
            raise SettingsError("ACCOUNT_DATABASE_URL must include a host and database name")
        if "*" in self.cors_origins:
            raise SettingsError("Wildcard CORS is forbidden")
        if self.allow_ephemeral_keys and self.environment != "test":
            raise SettingsError("Ephemeral account keys are allowed only in test")
        if self.community_deletion_url is not None:
            deletion_url = urlparse(self.community_deletion_url)
            if (
                deletion_url.scheme not in {"http", "https"}
                or not deletion_url.hostname
                or deletion_url.username
                or deletion_url.password
                or deletion_url.query
                or deletion_url.fragment
                or deletion_url.path != "/api/internal/account-deletions"
            ):
                raise SettingsError("COMMUNITY_DELETION_URL must be an absolute safe URL")
            if self.is_production and deletion_url.scheme == "http":
                hostname = deletion_url.hostname.lower()
                private_service = bool(
                    deletion_url.port
                    and hostname != "localhost"
                    and _is_internal_service_host(hostname)
                )
                if not private_service:
                    raise SettingsError(
                        "Production COMMUNITY_DELETION_URL HTTP is allowed only "
                        "for an explicit private service hostname and port"
                    )

        configured_secret_files = (
            ("ACCOUNT_SIGNING_KEY_FILE", self.signing_key_file),
            ("ACCOUNT_ENCRYPTION_KEY_FILE", self.encryption_key_file),
            ("INTERNAL_SERVICE_TOKEN_FILE", self.internal_service_token_file),
        )
        for name, path in configured_secret_files:
            if path is not None:
                self._validate_secret_file(name, path)

        if not self.is_production:
            return

        if self.auth_provider_mode == "mock":
            raise SettingsError("AUTH_PROVIDER_MODE=mock is forbidden in production")
        if issuer.scheme != "https":
            raise SettingsError("Production ACCOUNT_ISSUER must use HTTPS")
        if self.community_deletion_url is None:
            raise SettingsError("Production requires COMMUNITY_DELETION_URL")
        query = parse_qs(database.query)
        ssl_mode = query.get("sslmode", [""])[-1].lower()
        database_host = database.hostname.lower()
        database_is_loopback = database_host in _LOOPBACK_DATABASE_HOSTS
        database_is_internal_service = bool(
            database_host != "localhost"
            and _is_internal_service_host(database_host)
        )
        if self.database_is_local and not (
            database_is_loopback or database_is_internal_service
        ):
            raise SettingsError(
                "ACCOUNT_DATABASE_LOCAL=true requires loopback or an internal "
                "single-label database hostname"
            )
        if not (
            database_is_loopback
            or (self.database_is_local and database_is_internal_service)
            or ssl_mode in _TLS_DATABASE_MODES
        ):
            raise SettingsError(
                "Production PostgreSQL must use TLS unless ACCOUNT_DATABASE_LOCAL=true is explicit"
            )

        for name, path in configured_secret_files:
            if path is None:
                raise SettingsError(f"Production requires {name}")

    @staticmethod
    def _validate_secret_file(name: str, path: Path | None) -> None:
        if path is None:
            raise SettingsError(f"Production requires {name}")
        if not path.is_absolute() or not path.is_file():
            raise SettingsError(f"{name} must be an existing absolute file")
        permissions = stat.S_IMODE(path.stat().st_mode)
        # The internal service token is intentionally shared read-only with
        # the application container through a dedicated group/volume. Account
        # signing and encryption keys remain owner-readable only.
        forbidden_permissions = 0o027 if name == "INTERNAL_SERVICE_TOKEN_FILE" else 0o077
        if permissions & forbidden_permissions:
            raise SettingsError(f"{name} must not be group/world accessible")
