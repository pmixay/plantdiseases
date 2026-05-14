"""Coverage for the /api/feedback endpoint and feedback DB module.

Tests run against an isolated per-test SQLite file so writes from one
test never leak into the next. The endpoint surface and the DB module
are exercised separately:

  * Endpoint tests cover request shape, validation rules, and the
    response contract.
  * DB-module tests cover the metrics aggregation surface and the
    forward-only schema migration.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def isolated_feedback_db(tmp_path, monkeypatch):
    """Point the feedback store at a temp DB per test so writes can't leak.

    Bypasses the issued-scan-id gate by default — most contract tests
    don't care about the gate and would otherwise need to register
    every scan_id they POST. The dedicated gate tests
    (``test_feedback_endpoint_rejects_unissued_scan_id`` and friends)
    undo this patch.
    """
    db_path = tmp_path / "feedback.db"
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(db_path))
    monkeypatch.setenv("FEEDBACK_IP_PEPPER", "test-pepper")
    import importlib

    import feedback as fb
    importlib.reload(fb)
    # Re-bind the symbols main.py imported at module-load time so the
    # endpoint hits the freshly-reloaded module (which sees the temp DB).
    import main as server_main
    server_main.record_feedback = fb.record_feedback
    server_main.init_feedback_db = fb.init_db
    server_main.feedback_counts = fb.feedback_counts
    server_main.record_issued_scan_id = fb.record_issued_scan_id
    monkeypatch.setattr(server_main, "is_scan_id_issued", lambda *a, **k: True)
    yield db_path


# ── Happy-path contract ─────────────────────────────────────────────


def test_feedback_endpoint_accepts_minimal_payload(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-0001", "correct": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["id"], int) and body["id"] > 0


def test_feedback_endpoint_accepts_full_metadata(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "abcdef0123",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "note": "white powder on the upper side of the leaf",
            "reason": "wrong_class",
            "predicted_class": "gray_mold_botrytis",
            "predicted_confidence": 0.42,
            "top3": [
                {"class": "gray_mold_botrytis", "prob": 0.42},
                {"class": "powdery_mildew", "prob": 0.31},
                {"class": "healthy", "prob": 0.18},
            ],
            "model_version": "2.1",
            "app_version": "2.1.0",
            "locale": "en",
            "platform": "android",
            "user_pseudo_id": "pseudo-abcdef12",
            "region_count": 3,
            "primary_region_index": 1,
            "feedback_latency_ms": 4_500,
            "image_hash": "a" * 64,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ── Validation guard-rails ──────────────────────────────────────────


def test_feedback_endpoint_rejects_oversize_note(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-0001", "correct": False, "note": "x" * 5000},
    )
    assert r.status_code == 422


def test_feedback_endpoint_requires_scan_id(client, isolated_feedback_db):
    r = client.post("/api/feedback", json={"correct": True})
    assert r.status_code == 422


def test_feedback_endpoint_rejects_short_scan_id(client, isolated_feedback_db):
    r = client.post("/api/feedback", json={"scan_id": "abc", "correct": True})
    assert r.status_code == 422


def test_feedback_endpoint_rejects_bad_scan_id_chars(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan id with spaces", "correct": True},
    )
    assert r.status_code == 422


def test_feedback_endpoint_rejects_unknown_corrected_class(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": False,
            "corrected_class": "alien_invasion",
        },
    )
    assert r.status_code == 422


def test_feedback_endpoint_accepts_deferred_class(client, isolated_feedback_db):
    # 'edema' is deferred until v2.x but should still be accepted as
    # a correction signal for the retraining loop.
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": False,
            "corrected_class": "edema",
        },
    )
    assert r.status_code == 200


def test_feedback_endpoint_rejects_unknown_reason(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-0001", "correct": False, "reason": "I felt like it"},
    )
    assert r.status_code == 422


def test_feedback_endpoint_normalises_unknown_platform(client, isolated_feedback_db):
    # An unknown platform tag should NOT 422 — we'd rather collect the
    # signal than bounce a client with a typo in BuildConfig.
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "platform": "android-tablet",
        },
    )
    assert r.status_code == 200


def test_feedback_endpoint_rejects_oversize_top3(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "top3": [
                {"class": "a", "prob": 0.1},
                {"class": "b", "prob": 0.1},
                {"class": "c", "prob": 0.1},
                {"class": "d", "prob": 0.1},
            ],
        },
    )
    assert r.status_code == 422


def test_feedback_endpoint_rejects_non_hex_image_hash(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "image_hash": "z" * 64,
        },
    )
    assert r.status_code == 422


def test_feedback_endpoint_rejects_negative_latency(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "feedback_latency_ms": -1,
        },
    )
    assert r.status_code == 422


# ── Privacy: raw IP never lands on disk ─────────────────────────────


def test_feedback_endpoint_hashes_client_ip(client, isolated_feedback_db):
    client.post("/api/feedback", json={"scan_id": "scan-0001", "correct": True})
    with sqlite3.connect(str(isolated_feedback_db)) as conn:
        row = conn.execute(
            "SELECT client_ip, client_ip_hash FROM feedback ORDER BY id DESC LIMIT 1"
        ).fetchone()
    raw_ip, ip_hash = row
    assert raw_ip is None, "raw client IP must not be persisted"
    assert ip_hash and len(ip_hash) == 16, "hashed IP must be a short opaque token"


# ── Aggregation surface ─────────────────────────────────────────────


def test_feedback_endpoint_persists_and_aggregates(client, isolated_feedback_db):
    import feedback as fb

    client.post("/api/feedback", json={"scan_id": "scan-0001", "correct": True})
    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0002",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "note": "Looks like white powder, not gray mold.",
            "predicted_class": "gray_mold_botrytis",
            "reason": "wrong_class",
        },
    )
    client.post("/api/feedback", json={"scan_id": "scan-0003", "correct": False})

    counts = fb.feedback_counts()
    assert counts["total"] == 3
    assert counts["correct"] == 1
    assert counts["incorrect"] == 2
    assert counts["last_24h"] == 3
    assert counts["last_7d"] == 3
    # per-class accuracy is only computed when predicted_class is sent.
    assert "gray_mold_botrytis" in counts["per_class"]
    assert counts["per_class"]["gray_mold_botrytis"]["total"] == 1
    assert counts["per_class"]["gray_mold_botrytis"]["accuracy"] == 0.0
    assert counts["top_corrections"]["gray_mold_botrytis"]["powdery_mildew"] == 1
    assert counts["reasons"]["wrong_class"] == 1


def test_feedback_counts_surface_on_metrics(client, isolated_feedback_db):
    client.post("/api/feedback", json={"scan_id": "scan-0001", "correct": True})
    m = client.get("/api/metrics").json()
    assert "feedback" in m
    assert m["feedback"]["total"] >= 1
    assert "per_class" in m["feedback"]
    assert "top_corrections" in m["feedback"]


# ── Forward-only schema migration ───────────────────────────────────


def test_init_db_migrates_legacy_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "feedback.db"
    # Set up a legacy-shape table (only the original 7 columns) plus
    # the issued_scan_ids table so we can mark the legacy row as
    # minted — without that, the one-shot cleanup would drop it.
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                correct INTEGER NOT NULL,
                corrected_class TEXT,
                note TEXT,
                client_ip TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE issued_scan_ids (
                scan_id    TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO feedback (scan_id, correct, created_at) VALUES (?, ?, ?)",
            ("legacy-001", 1, 0),
        )
        conn.execute(
            "INSERT INTO issued_scan_ids (scan_id, created_at) VALUES (?, ?)",
            ("legacy-001", 0),
        )
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(db_path))
    import importlib

    import feedback as fb
    importlib.reload(fb)
    fb.init_db()

    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(feedback)").fetchall()}
    # Every new column from the upgrade should now be present, and the
    # original legacy row must still be there.
    assert "predicted_class" in cols
    assert "user_pseudo_id" in cols
    assert "client_ip_hash" in cols
    with sqlite3.connect(str(db_path)) as conn:
        legacy_count = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE scan_id = 'legacy-001'"
        ).fetchone()[0]
    assert legacy_count == 1


def test_metrics_aggregation_handles_empty_db(client, isolated_feedback_db):
    import feedback as fb

    counts = fb.feedback_counts()
    assert counts["total"] == 0
    assert counts["per_class"] == {}
    assert counts["top_corrections"] == {}
    assert counts["reasons"] == {}
    assert counts["avg_latency_ms"] is None


# ── Issued-scan-id gate ─────────────────────────────────────────────


def test_feedback_endpoint_rejects_unissued_scan_id(client, isolated_feedback_db, monkeypatch):
    # Undo the fixture's permissive patch so the real gate runs.
    import feedback as fb
    import main as server_main
    monkeypatch.setattr(server_main, "is_scan_id_issued", fb.is_scan_id_issued)

    r = client.post(
        "/api/feedback",
        json={"scan_id": "definitelynevermintedabcdef0123", "correct": False,
              "corrected_class": "healthy"},
    )
    assert r.status_code == 422, r.text
    assert "scan_id" in r.json()["detail"].lower()


def test_feedback_endpoint_accepts_issued_scan_id(client, isolated_feedback_db, monkeypatch):
    import feedback as fb
    import main as server_main
    monkeypatch.setattr(server_main, "is_scan_id_issued", fb.is_scan_id_issued)
    fb.record_issued_scan_id("scan-issued-001")

    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-issued-001",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "image_hash": "b" * 64,
        },
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(str(isolated_feedback_db)) as conn:
        row = conn.execute(
            "SELECT scan_id, image_hash FROM feedback ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == ("scan-issued-001", "b" * 64)


# ── Legacy cleanup ──────────────────────────────────────────────────


def test_cleanup_legacy_rows_spares_valid(isolated_feedback_db):
    import feedback as fb

    # init_db (already triggered by the fixture) has installed the
    # sentinel, so the one-shot cleanup is a no-op now and we can
    # seed any rows we want for direct testing.
    fb.record_issued_scan_id("scan-valid-001")
    with sqlite3.connect(str(isolated_feedback_db)) as conn:
        # Legacy row: raw client_ip still set, no client_ip_hash.
        conn.execute(
            "INSERT INTO feedback (scan_id, correct, client_ip, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("1", 1, "172.29.172.2", 0),
        )
        # Test/poison row: scan_id was never minted.
        conn.execute(
            "INSERT INTO feedback (scan_id, correct, client_ip_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("definitelynevermintedabcdef0123", 0, "deadbeefcafebabe", 0),
        )
        # Valid row: hashed IP only, minted scan_id.
        conn.execute(
            "INSERT INTO feedback (scan_id, correct, client_ip_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("scan-valid-001", 1, "deadbeefcafebabe", 0),
        )

    n = fb.cleanup_legacy_rows()
    assert n == 2

    with sqlite3.connect(str(isolated_feedback_db)) as conn:
        survivors = {
            row[0]
            for row in conn.execute("SELECT scan_id FROM feedback").fetchall()
        }
    assert survivors == {"scan-valid-001"}

    # Idempotent: a second call deletes nothing.
    assert fb.cleanup_legacy_rows() == 0


def test_init_db_runs_cleanup_once(tmp_path, monkeypatch):
    db_path = tmp_path / "feedback.db"
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(db_path))
    import importlib

    import feedback as fb
    importlib.reload(fb)

    # First boot creates the schema, runs the (empty) cleanup, and
    # writes the sentinel.
    fb.init_db()
    with sqlite3.connect(str(db_path)) as conn:
        sentinel = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'legacy_cleanup_v1'"
        ).fetchone()
    assert sentinel is not None

    # Seed a legacy-looking row directly, then force re-init by
    # clearing the in-process flag. The cleanup must NOT run again
    # (sentinel guards it) so the row survives.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO feedback (scan_id, correct, client_ip, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("1", 1, "1.2.3.4", 0),
        )
    fb._initialised = False
    fb.init_db()
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE scan_id = '1'"
        ).fetchone()[0]
    assert count == 1, "cleanup must not re-run once sentinel is set"
