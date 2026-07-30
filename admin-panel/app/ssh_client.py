from __future__ import annotations

import base64
import hashlib
import json
import shlex
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paramiko

from .models import ConnectionRequest


MAX_OUTPUT_BYTES = 512 * 1024


class SshOperationError(RuntimeError):
    pass


def fingerprint_for_key(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected: str):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):  # noqa: ANN001
        actual = fingerprint_for_key(key)
        if not secrets_equal(actual, self.expected):
            raise paramiko.SSHException(
                f"SSH host key mismatch for {hostname}: expected {self.expected}, received {actual}",
            )


def secrets_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.strip(), right.strip())


@dataclass
class StoredConnection:
    host: str
    port: int
    username: str
    password: str | None
    key_path: str | None
    repo_path: str
    data_dir: str
    remote_python: str
    fingerprint: str
    deployment_method: str
    service_name: str
    compose_file: str
    custom_restart_command: str | None
    health_url: str

    @classmethod
    def from_request(cls, request: ConnectionRequest) -> "StoredConnection":
        return cls(
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password.get_secret_value() if request.password else None,
            key_path=request.key_path,
            repo_path=request.repo_path,
            data_dir=request.data_dir,
            remote_python=request.remote_python,
            fingerprint=request.expected_fingerprint,
            deployment_method=request.deployment_method,
            service_name=request.service_name,
            compose_file=request.compose_file,
            custom_restart_command=request.custom_restart_command,
            health_url=request.health_url,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_method": "private_key" if self.key_path else "password",
            "key_path": self.key_path,
            "repo_path": self.repo_path,
            "data_dir": self.data_dir,
            "remote_python": self.remote_python,
            "fingerprint": self.fingerprint,
            "deployment_method": self.deployment_method,
            "service_name": self.service_name,
            "compose_file": self.compose_file,
            "health_url": self.health_url,
        }


class SshManager:
    def __init__(self):
        self._connections: dict[str, StoredConnection] = {}
        self._lock = threading.RLock()

    @staticmethod
    def probe(host: str, port: int, timeout: float = 8.0) -> dict[str, str]:
        sock = None
        transport = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            transport = paramiko.Transport(sock)
            transport.banner_timeout = timeout
            transport.start_client(timeout=timeout)
            key = transport.get_remote_server_key()
            return {
                "algorithm": key.get_name(),
                "fingerprint": fingerprint_for_key(key),
            }
        except (OSError, paramiko.SSHException) as exc:
            raise SshOperationError(f"Could not read the SSH host key: {exc}") from exc
        finally:
            if transport:
                transport.close()
            elif sock:
                sock.close()

    def connect(self, session_id: str, request: ConnectionRequest) -> dict[str, Any]:
        connection = StoredConnection.from_request(request)
        client = self._new_client(connection)
        try:
            result = self._execute_on_client(client, "printf 'connected'", timeout=10)
            if result["exit_code"] != 0:
                raise SshOperationError("SSH authentication succeeded but the remote shell failed")
        finally:
            client.close()
        with self._lock:
            self._connections[session_id] = connection
        return connection.public_dict()

    def disconnect(self, session_id: str) -> None:
        with self._lock:
            self._connections.pop(session_id, None)

    def get(self, session_id: str) -> StoredConnection:
        with self._lock:
            connection = self._connections.get(session_id)
        if not connection:
            raise SshOperationError("No SSH server is connected")
        return connection

    def public_connection(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            connection = self._connections.get(session_id)
        return connection.public_dict() if connection else None

    def run(
        self,
        session_id: str,
        command: str,
        *,
        timeout: int = 60,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        connection = self.get(session_id)
        client = self._new_client(connection)
        try:
            return self._execute_on_client(
                client,
                command,
                timeout=timeout,
                stdin_data=stdin_data,
            )
        finally:
            client.close()

    def run_remote_agent(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        timeout: int = 90,
    ) -> dict[str, Any]:
        source_path = Path(__file__).with_name("remote_agent.py")
        source = source_path.read_text(encoding="utf-8")
        prelude = "REQUEST_JSON = " + repr(json.dumps(request, separators=(",", ":"))) + "\n"
        result = self.run(
            session_id,
            f"{shlex.quote(self.get(session_id).remote_python)} -",
            timeout=timeout,
            stdin_data=prelude + source,
        )
        if result["exit_code"] != 0:
            message = result["stderr"].strip() or "remote database helper failed"
            raise SshOperationError(message[-2000:])
        try:
            payload = json.loads(result["stdout"])
        except json.JSONDecodeError as exc:
            raise SshOperationError("Remote database helper returned invalid JSON") from exc
        if not payload.get("ok", False):
            error = payload.get("error") or {}
            raise SshOperationError(str(error.get("message") or "remote database operation failed"))
        return payload

    @staticmethod
    def _new_client(connection: StoredConnection) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(FingerprintPolicy(connection.fingerprint))
        try:
            client.connect(
                hostname=connection.host,
                port=connection.port,
                username=connection.username,
                password=connection.password,
                key_filename=connection.key_path,
                timeout=10,
                banner_timeout=10,
                auth_timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )
            return client
        except (OSError, paramiko.SSHException) as exc:
            client.close()
            raise SshOperationError(f"SSH connection failed: {exc}") from exc

    @staticmethod
    def _execute_on_client(
        client: paramiko.SSHClient,
        command: str,
        *,
        timeout: int,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise SshOperationError("SSH transport is not active")
        channel = transport.open_session(timeout=10)
        channel.exec_command(command)
        if stdin_data is not None:
            channel.sendall(stdin_data.encode("utf-8"))
        channel.shutdown_write()

        stdout = bytearray()
        stderr = bytearray()
        stdout_truncated = False
        stderr_truncated = False
        while True:
            while channel.recv_ready():
                chunk = channel.recv(32768)
                if len(stdout) < MAX_OUTPUT_BYTES:
                    remaining = MAX_OUTPUT_BYTES - len(stdout)
                    stdout.extend(chunk[:remaining])
                    stdout_truncated = stdout_truncated or len(chunk) > remaining
                else:
                    stdout_truncated = True
            while channel.recv_stderr_ready():
                chunk = channel.recv_stderr(32768)
                if len(stderr) < MAX_OUTPUT_BYTES:
                    remaining = MAX_OUTPUT_BYTES - len(stderr)
                    stderr.extend(chunk[:remaining])
                    stderr_truncated = stderr_truncated or len(chunk) > remaining
                else:
                    stderr_truncated = True
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() - started > timeout:
                channel.close()
                raise SshOperationError(f"Remote command timed out after {timeout} seconds")
            time.sleep(0.03)

        exit_code = channel.recv_exit_status()
        channel.close()
        return {
            "exit_code": exit_code,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
