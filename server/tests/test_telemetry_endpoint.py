"""Coverage for /api/telemetry/errors and the telemetry.py store."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def teldb(tmp_path, monkeypatch):
    """Point the telemetry store at a fresh per-test DB."""
    import telemetry
    monkeypatch.setattr(telemetry, "TELEMETRY_DB_PATH", tmp_path / "telemetry.db")
    monkeypatch.setattr(telemetry, "_initialised", False)
    telemetry.init_db()
    # main.py bound these at import; they proxy the reloaded module functions.
    import main as server_main
    monkeypatch.setattr(server_main, "TELEMETRY_ENABLED", True)
    return telemetry


def _rows(telemetry):
    with sqlite3.connect(str(telemetry.TELEMETRY_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM error_reports ORDER BY id")]


def test_error_report_recorded(client, teldb):
    r = client.post(
        "/api/telemetry/errors",
        json={
            "stage": "analyze",
            "kind": "timeout",
            "http_status": 504,
            "family": "gamma",
            "app_version": "2.2",
            "android_api": 34,
            "message": "read timed out",
            "breadcrumbs": ["opened_scanner", "started_analyze"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "recorded": True}
    rows = _rows(teldb)
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "analyze" and row["kind"] == "timeout"
    assert row["http_status"] == 504 and row["family"] == "gamma"
    assert "started_analyze" in row["breadcrumbs"]
    # An IP hash is stored (for the daily cap), never a raw IP.
    assert row["client_ip_hash"]


def test_minimal_error_report(client, teldb):
    r = client.post("/api/telemetry/errors", json={"stage": "aqua", "kind": "oom"})
    assert r.status_code == 200, r.text
    assert len(_rows(teldb)) == 1


def test_missing_required_field_rejected(client, teldb):
    assert client.post("/api/telemetry/errors", json={"kind": "timeout"}).status_code == 422


def test_disabled_records_nothing(client, teldb, monkeypatch):
    import main as server_main
    monkeypatch.setattr(server_main, "TELEMETRY_ENABLED", False)
    r = client.post("/api/telemetry/errors", json={"stage": "analyze", "kind": "network"})
    assert r.status_code == 200
    assert r.json()["recorded"] is False
    assert _rows(teldb) == []


def test_daily_cap_returns_429(client, teldb, monkeypatch):
    import main as server_main
    monkeypatch.setattr(server_main, "TELEMETRY_DAILY_CAP_PER_IP", 1)
    first = client.post("/api/telemetry/errors", json={"stage": "analyze", "kind": "network"})
    assert first.status_code == 200
    second = client.post("/api/telemetry/errors", json={"stage": "analyze", "kind": "network"})
    assert second.status_code == 429


def test_breadcrumbs_and_message_capped(client, teldb):
    r = client.post(
        "/api/telemetry/errors",
        json={
            "stage": "analyze",
            "kind": "server_5xx",
            "message": "x" * 5000,
            "breadcrumbs": [f"crumb_{i}" for i in range(100)],
        },
    )
    # Pydantic caps breadcrumbs at 40 items; longer lists are rejected (422).
    assert r.status_code == 422, r.text


def test_error_counts_groups_by_stage_kind(teldb):
    teldb.record_error(stage="analyze", kind="timeout")
    teldb.record_error(stage="analyze", kind="timeout")
    teldb.record_error(stage="aqua", kind="oom")
    counts = {(c["stage"], c["kind"]): c["n"] for c in teldb.error_counts(days=7)}
    assert counts[("analyze", "timeout")] == 2
    assert counts[("aqua", "oom")] == 1
