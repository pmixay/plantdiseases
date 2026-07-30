from __future__ import annotations

import pytest

import app.ssh_client as ssh_module
from app.ssh_client import SshManager, SshOperationError, StoredConnection


def connection() -> StoredConnection:
    return StoredConnection(
        host="server.example.com",
        port=22,
        username="admin",
        password="secret",
        key_path=None,
        repo_path="/srv/plantdiseases",
        data_dir="/srv/plantdiseases/server",
        remote_python="/usr/bin/python3",
        fingerprint="SHA256:abcdefghijklmnopqrstuvwxyz0123456789",
        deployment_method="systemd",
        service_name="plantdiseases.service",
        compose_file="server/docker-compose.yml",
        custom_restart_command=None,
        health_url="http://127.0.0.1:8000/api/health",
    )


class FakeTransport:
    def __init__(self, active: bool = True) -> None:
        self.active = active

    def is_active(self) -> bool:
        return self.active

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive_seconds = seconds


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.transport = FakeTransport()

    def set_missing_host_key_policy(self, policy) -> None:  # noqa: ANN001
        self.policy = policy

    def connect(self, **kwargs) -> None:  # noqa: ANN003
        self.connect_kwargs = kwargs

    def get_transport(self) -> FakeTransport:
        return self.transport

    def close(self) -> None:
        self.closed = True
        self.transport.active = False


def test_connection_retries_temporary_failures(monkeypatch) -> None:
    attempts = []

    class FlakyClient(FakeClient):
        def connect(self, **kwargs) -> None:  # noqa: ANN003
            attempts.append(kwargs)
            if len(attempts) < 3:
                raise OSError("timed out")
            super().connect(**kwargs)

    monkeypatch.setattr(ssh_module.paramiko, "SSHClient", FlakyClient)
    monkeypatch.setattr(ssh_module.time, "sleep", lambda seconds: None)

    client = SshManager._new_client(connection())

    assert isinstance(client, FlakyClient)
    assert len(attempts) == 3
    assert client.transport.keepalive_seconds == ssh_module.KEEPALIVE_SECONDS


def test_authentication_failure_is_not_retried(monkeypatch) -> None:
    attempts = 0

    class DeniedClient(FakeClient):
        def connect(self, **kwargs) -> None:  # noqa: ANN003
            nonlocal attempts
            attempts += 1
            raise ssh_module.paramiko.AuthenticationException("denied")

    monkeypatch.setattr(ssh_module.paramiko, "SSHClient", DeniedClient)

    with pytest.raises(SshOperationError, match="after 1 attempt"):
        SshManager._new_client(connection())

    assert attempts == 1


def test_commands_reuse_one_active_client(monkeypatch) -> None:
    manager = SshManager()
    remote = connection()
    manager._connections["session"] = remote
    created = []

    def new_client(_connection):  # noqa: ANN001
        client = FakeClient()
        created.append(client)
        return client

    monkeypatch.setattr(manager, "_new_client", new_client)
    monkeypatch.setattr(
        manager,
        "_execute_on_client",
        lambda client, command, timeout, stdin_data=None: {"exit_code": 0, "stdout": command},
    )

    assert manager.run("session", "first")["stdout"] == "first"
    assert manager.run("session", "second")["stdout"] == "second"

    assert len(created) == 1


def test_disconnect_closes_retained_client() -> None:
    manager = SshManager()
    client = FakeClient()
    manager._connections["session"] = connection()
    manager._clients["session"] = client

    manager.disconnect("session")

    assert client.closed
    assert "session" not in manager._connections
    assert "session" not in manager._clients
