"""Environment configuration with fail-closed production safety checks."""

from __future__ import annotations

import os
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
            database_is_local=source.get("ACCOUNT_DATABASE_LOCAL", "").strip().lower()
            in {"1", "true", "yes", "on"},
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

        if not self.is_production:
            return

        if self.auth_provider_mode == "mock":
            raise SettingsError("AUTH_PROVIDER_MODE=mock is forbidden in production")
        if issuer.scheme != "https":
            raise SettingsError("Production ACCOUNT_ISSUER must use HTTPS")
        query = parse_qs(database.query)
        ssl_mode = query.get("sslmode", [""])[-1].lower()
        database_is_loopback = database.hostname in _LOOPBACK_DATABASE_HOSTS
        if not (self.database_is_local or database_is_loopback or ssl_mode in _TLS_DATABASE_MODES):
            raise SettingsError(
                "Production PostgreSQL must use TLS unless ACCOUNT_DATABASE_LOCAL=true is explicit"
            )

        self._validate_secret_file("ACCOUNT_SIGNING_KEY_FILE", self.signing_key_file)
        self._validate_secret_file("ACCOUNT_ENCRYPTION_KEY_FILE", self.encryption_key_file)
        self._validate_secret_file(
            "INTERNAL_SERVICE_TOKEN_FILE", self.internal_service_token_file
        )

    @staticmethod
    def _validate_secret_file(name: str, path: Path | None) -> None:
        if path is None:
            raise SettingsError(f"Production requires {name}")
        if not path.is_absolute() or not path.is_file():
            raise SettingsError(f"{name} must be an existing absolute file")
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            raise SettingsError(f"{name} must not be group/world accessible")
