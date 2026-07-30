from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from app.models import BranchRequest, ConnectionRequest, ProbeRequest
from app.security import LocalSessionStore


def connection_payload(**overrides):
    payload = {
        "host": "server.example.com",
        "port": 22,
        "username": "plantd",
        "password": "secret",
        "repo_path": "/opt/plantdiseases",
        "data_dir": "/var/lib/plantdiseases",
        "expected_fingerprint": "SHA256:abcdefghijklmnopqrstuvwxyz0123456789",
        "deployment_method": "docker_compose",
        "service_name": "plantdiseases",
        "compose_file": "server/docker-compose.yml",
        "health_url": "http://127.0.0.1:8000/api/health",
    }
    payload.update(overrides)
    return payload


def test_connection_requires_authentication() -> None:
    with pytest.raises(ValidationError):
        ConnectionRequest.model_validate(connection_payload(password=None, key_path=None))


@pytest.mark.parametrize("path", ["relative/path", "../server", "", "C:\\server"])
def test_remote_paths_must_be_absolute_posix(path: str) -> None:
    with pytest.raises(ValidationError):
        ConnectionRequest.model_validate(connection_payload(repo_path=path))


@pytest.mark.parametrize("compose", ["../docker-compose.yml", "/tmp/compose.yml", "a/../../b.yml"])
def test_compose_path_rejects_traversal(compose: str) -> None:
    with pytest.raises(ValidationError):
        ConnectionRequest.model_validate(connection_payload(compose_file=compose))


@pytest.mark.parametrize("host", ["https://host", "host/path", "bad host", ""])
def test_probe_rejects_non_host_input(host: str) -> None:
    with pytest.raises(ValidationError):
        ProbeRequest(host=host, port=22)


def test_branch_rejects_control_characters() -> None:
    with pytest.raises(ValidationError):
        BranchRequest(branch="main\nrm -rf something")


def test_custom_restart_requires_command() -> None:
    with pytest.raises(ValidationError):
        ConnectionRequest.model_validate(connection_payload(deployment_method="custom", custom_restart_command=""))


def test_local_session_bootstrap_and_csrf() -> None:
    store = LocalSessionStore(ttl_seconds=60, max_sessions=2)
    printed_token = store.bootstrap_token
    assert store.valid_bootstrap_token(printed_token)
    assert not store.valid_bootstrap_token("wrong")
    assert store.consume_bootstrap_token(printed_token)
    assert not store.valid_bootstrap_token(printed_token)
    session = store.create()
    assert store.get(session.session_id) is session
    assert store.check_csrf(session, session.csrf_token)
    assert not store.check_csrf(session, "wrong")
    store.remove(session.session_id)
    assert store.get(session.session_id) is None


def test_local_session_expires() -> None:
    store = LocalSessionStore(ttl_seconds=1)
    session = store.create()
    session.last_seen_at = time.time() - 2
    assert store.get(session.session_id) is None
