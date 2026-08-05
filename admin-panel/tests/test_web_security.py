from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

import app.main as main_module
from app.main import app, sessions, ssh


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


async def bootstrap(client: httpx.AsyncClient) -> str:
    response = await client.get(
        f"/bootstrap?token={sessions.bootstrap_token}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    session = await client.get("/api/session")
    assert session.status_code == 200
    return session.json()["csrf_token"]


def run_scenario(callback) -> None:
    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await callback(client)

    asyncio.run(scenario())


def test_root_requires_launcher_bootstrap() -> None:
    async def check(client):
        response = await client.get("/")
        assert response.status_code == 401
        assert "Launch token required" in response.text
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

    run_scenario(check)


def test_mutation_requires_csrf(monkeypatch) -> None:
    async def direct_call(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(main_module, "run_in_threadpool", direct_call)
    monkeypatch.setattr(
        ssh,
        "probe",
        lambda host, port: {"algorithm": "ssh-ed25519", "fingerprint": "SHA256:test"},
    )

    async def check(client):
        csrf = await bootstrap(client)
        body = {"host": "server.example.com", "port": 22}
        rejected = await client.post("/api/connection/probe", json=body)
        assert rejected.status_code == 403
        accepted = await client.post(
            "/api/connection/probe",
            json=body,
            headers={"X-CSRF-Token": csrf},
        )
        assert accepted.status_code == 200
        assert accepted.json()["fingerprint"] == "SHA256:test"

    run_scenario(check)


def test_cross_origin_mutation_is_rejected(monkeypatch) -> None:
    async def direct_call(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(main_module, "run_in_threadpool", direct_call)
    monkeypatch.setattr(
        ssh,
        "probe",
        lambda host, port: {"algorithm": "ssh-ed25519", "fingerprint": "SHA256:test"},
    )

    async def check(client):
        csrf = await bootstrap(client)
        response = await client.post(
            "/api/connection/probe",
            json={"host": "server.example.com", "port": 22},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        assert response.status_code == 403

    run_scenario(check)


def test_api_responses_are_not_cached() -> None:
    async def check(client):
        await bootstrap(client)
        response = await client.get("/api/session")
        assert response.headers["cache-control"] == "no-store"

    run_scenario(check)


def test_connection_footer_targets_detail_instead_of_status_dot() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'class="connection-detail"' in html
    assert '$(".connection-detail", pill)' in javascript
    assert '$("div span", pill)' not in javascript
