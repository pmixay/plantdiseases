"""Consent and minimization contract for /api/telemetry/errors."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

import main as server_main
import telemetry


@pytest.fixture
def teldb(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "TELEMETRY_DB_PATH", tmp_path / "telemetry.db")
    monkeypatch.setattr(telemetry, "_initialised", False)
    monkeypatch.setattr(server_main, "TELEMETRY_ENABLED", True)
    telemetry.init_db()
    server_main._daily_write_counts.clear()
    return telemetry


def _rows(store):
    with closing(sqlite3.connect(store.TELEMETRY_DB_PATH)) as connection, connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute("SELECT * FROM error_reports ORDER BY id")
        ]


def _consented(**values):
    return {
        "telemetry_consent": True,
        "telemetry_consent_version": server_main.TELEMETRY_CONSENT_VERSION,
        "stage": "analyze",
        "kind": "timeout",
        **values,
    }


def test_no_consent_returns_success_without_writing(client, teldb):
    response = client.post(
        "/api/telemetry/errors", json={"stage": "analyze", "kind": "timeout"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "recorded": False}
    assert _rows(teldb) == []
    assert server_main._daily_write_counts == {}


def test_stale_consent_version_is_rejected_without_writing(client, teldb):
    payload = _consented()
    payload["telemetry_consent_version"] = "telemetry-errors-v0"
    response = client.post("/api/telemetry/errors", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "telemetry_consent_version_invalid"
    assert _rows(teldb) == []


def test_consented_report_stores_only_coarse_allowlisted_dimensions(client, teldb):
    response = client.post(
        "/api/telemetry/errors",
        json=_consented(
            http_status=504,
            family="gamma",
            app_version="2.6.9-beta+7",
            android_api=34,
            breadcrumbs=["scanner_opened", "analyze_started_gamma"],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "recorded": True}
    row = _rows(teldb)[0]
    assert row["stage"] == "analyze"
    assert row["kind"] == "timeout"
    assert row["http_status_bucket"] == "5xx"
    assert row["android_api_bucket"] == "33-34"
    assert row["family"] == "gamma"
    assert row["breadcrumbs_json"] == '["scanner_opened","analyze_started_gamma"]'
    assert row["created_day"] == datetime.now(UTC).date().isoformat()

    schema = teldb.schema_sql().lower()
    for forbidden in (
        "message",
        "client_ip",
        "user_pseudo_id",
        "installation_id",
        "account_id",
        "nickname",
        "device_model",
        "manufacturer",
        "request_body",
    ):
        assert forbidden not in schema


@pytest.mark.parametrize(
    "extra",
    [
        {"message": "SENTINEL /private/path?token=secret"},
        {"client_ip_hash": "SENTINEL-IP-HASH"},
        {"device_model": "SENTINEL-PHONE"},
        {"user_pseudo_id": "SENTINEL-INSTALLATION"},
    ],
)
def test_identifier_and_free_text_fields_are_forbidden(client, teldb, extra):
    response = client.post("/api/telemetry/errors", json=_consented(**extra))
    assert response.status_code == 422
    assert _rows(teldb) == []


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "arbitrary_stage"},
        {"kind": "java.lang.SomeException"},
        {"family": "custom-device-family"},
        {"breadcrumbs": ["opened:/private/user/path"]},
        {"app_version": "version with spaces"},
    ],
)
def test_non_allowlisted_dimensions_are_rejected(client, teldb, change):
    response = client.post("/api/telemetry/errors", json=_consented(**change))
    assert response.status_code == 422
    assert _rows(teldb) == []


def test_disabled_telemetry_records_nothing(client, teldb, monkeypatch):
    monkeypatch.setattr(server_main, "TELEMETRY_ENABLED", False)
    response = client.post("/api/telemetry/errors", json=_consented())
    assert response.status_code == 200
    assert response.json()["recorded"] is False
    assert _rows(teldb) == []


def test_daily_cap_uses_only_process_memory(client, teldb, monkeypatch):
    monkeypatch.setattr(server_main, "TELEMETRY_DAILY_CAP_PER_IP", 1)
    server_main._daily_write_counts.clear()
    assert client.post("/api/telemetry/errors", json=_consented()).status_code == 200
    response = client.post(
        "/api/telemetry/errors",
        json=_consented(kind="no_network"),
    )
    assert response.status_code == 429
    assert len(_rows(teldb)) == 1


def test_error_counts_group_only_safe_dimensions(teldb):
    teldb.record_error(
        stage="analyze",
        kind="timeout",
        family="gamma",
        http_status_bucket="5xx",
        android_api_bucket="33-34",
    )
    teldb.record_error(
        stage="analyze",
        kind="timeout",
        family="gamma",
        http_status_bucket="5xx",
        android_api_bucket="33-34",
    )
    counts = teldb.error_counts(days=7)
    assert counts == [
        {
            "stage": "analyze",
            "kind": "timeout",
            "fatal": 0,
            "family": "gamma",
            "http_status_bucket": "5xx",
            "android_api_bucket": "33-34",
            "n": 2,
        }
    ]


def test_legacy_schema_fails_closed_without_modification(tmp_path, monkeypatch):
    database = tmp_path / "legacy-telemetry.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE error_reports (id INTEGER PRIMARY KEY, message TEXT, "
            "client_ip_hash TEXT)"
        )
        connection.execute(
            "INSERT INTO error_reports VALUES (1, 'SENTINEL-FREE-TEXT', "
            "'SENTINEL-IP-HASH')"
        )
    before = database.read_bytes()
    monkeypatch.setattr(telemetry, "TELEMETRY_DB_PATH", database)
    monkeypatch.setattr(telemetry, "_initialised", False)
    with pytest.raises(telemetry.TelemetrySchemaError, match="explicit privacy audit"):
        telemetry.init_db()
    assert database.read_bytes() == before


def test_retention_uses_coarse_day(teldb):
    teldb.record_error(
        stage="analyze", kind="timeout", created_day="2020-01-01"
    )
    teldb.record_error(
        stage="analyze", kind="timeout", created_day="2026-08-06"
    )
    removed = teldb.prune_older_than(
        30, now=datetime(2026, 8, 7, tzinfo=UTC)
    )
    assert removed == 1
    assert [row["created_day"] for row in _rows(teldb)] == ["2026-08-06"]
