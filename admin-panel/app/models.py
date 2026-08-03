from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
_USER_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ProbeRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        host = value.strip()
        if not host or any(ch.isspace() for ch in host) or "/" in host or "://" in host:
            raise ValueError("enter a hostname or IP address without a URL scheme")
        return host


class ConnectionRequest(ProbeRequest):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = None
    key_path: str | None = Field(default=None, max_length=1024)
    repo_path: str = Field(default="/home/plantdiseases/repo", min_length=1, max_length=1024)
    data_dir: str = Field(default="/home/plantdiseases/repo/server", min_length=1, max_length=1024)
    remote_python: str = Field(default="/usr/bin/python3", min_length=1, max_length=1024)
    expected_fingerprint: str = Field(min_length=10, max_length=256)
    deployment_method: Literal["docker_compose", "systemd", "custom"] = "systemd"
    service_name: str = Field(default="plantdiseases.service", max_length=128)
    compose_file: str = Field(default="server/docker-compose.yml", max_length=512)
    custom_restart_command: str | None = Field(default=None, max_length=2000)
    health_url: str = Field(default="http://127.0.0.1:8000/api/health", max_length=2048)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("invalid SSH username")
        return value

    @field_validator("repo_path", "data_dir", "remote_python")
    @classmethod
    def absolute_remote_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("remote paths must be absolute POSIX paths")
        return value.rstrip("/") or "/"

    @field_validator("key_path")
    @classmethod
    def clean_key_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("service_name")
    @classmethod
    def safe_service(cls, value: str) -> str:
        value = value.strip()
        if not _SERVICE_RE.fullmatch(value):
            raise ValueError("invalid systemd service name")
        return value

    @field_validator("compose_file")
    @classmethod
    def safe_compose_path(cls, value: str) -> str:
        value = value.strip().replace("\\", "/")
        parts = [part for part in value.split("/") if part]
        if not value or value.startswith("/") or ".." in parts or "\x00" in value:
            raise ValueError("compose file must be a safe path relative to the repository")
        return value

    @field_validator("health_url")
    @classmethod
    def safe_health_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("health URL must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def require_auth_and_custom_command(self) -> "ConnectionRequest":
        has_password = bool(self.password and self.password.get_secret_value())
        if not has_password and not self.key_path:
            raise ValueError("provide an SSH password or private-key path")
        if self.deployment_method == "custom" and not (self.custom_restart_command or "").strip():
            raise ValueError("custom restart mode requires a command")
        return self


class ConsoleRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    timeout_seconds: int = Field(default=60, ge=1, le=300)

    @field_validator("command")
    @classmethod
    def clean_command(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("command contains a null byte")
        return value.strip()


class BranchRequest(BaseModel):
    branch: str = Field(min_length=1, max_length=240)

    @field_validator("branch")
    @classmethod
    def clean_branch(cls, value: str) -> str:
        value = value.strip()
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("invalid branch name")
        return value


class DeleteUserRequest(BaseModel):
    confirm_user_id: str
    create_backup: bool = True

    @field_validator("confirm_user_id")
    @classmethod
    def valid_user_id(cls, value: str) -> str:
        value = value.strip()
        if not _USER_RE.fullmatch(value):
            raise ValueError("invalid user ID")
        return value


class RestartRequest(BaseModel):
    confirm: bool = False


class DeployRequest(BranchRequest):
    restart: bool = True
    create_backup: bool = True


def validate_user_id(value: str) -> str:
    if not _USER_RE.fullmatch(value):
        raise ValueError("invalid user ID")
    return value
