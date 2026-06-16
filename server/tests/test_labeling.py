"""Tests for the human data-labeling backend (server/labeling.py + endpoints).

Scope: user feedback + recorded user scans only (dataset ingestion/export
were removed)."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

import labeling
import main as server_main

_seed_counter = 0


def _unique_jpeg() -> bytes:
    """Distinct bytes each call so the SHA-unique index doesn't collapse seeds.

    A distinct SOLID colour per seed — a 1-pixel tweak gets quantised away by
    JPEG, collapsing to identical bytes (same sha → ignored insert)."""
    global _seed_counter
    _seed_counter += 1
    c = _seed_counter
    img = Image.new("RGB", (64, 64), (c * 37 % 256, c * 101 % 256, c * 53 % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def labdb(monkeypatch, tmp_path):
    """Fresh labeling DB + a known admin password, feature enabled.

    Unique per-test DB + image dir: labeling leaks its WAL connections, so a
    shared path lets one test's rows survive into the next."""
    monkeypatch.setattr(labeling, "LABELING_DB_PATH", tmp_path / "labeling.db")
    monkeypatch.setattr(labeling, "LABELING_IMG_DIR", tmp_path / "images")
    labeling._initialised = False
    labeling.init_db()
    monkeypatch.setattr(labeling, "_admin_password", lambda: "s3cret")
    monkeypatch.setattr(server_main, "LABELING_ENABLED", True)
    labeling._login_attempts.clear()
    return labeling


def _seed(cls, status="pending", source="user_scan", scan_id=None):
    body = _unique_jpeg()
    sha = hashlib.sha256(body).hexdigest()
    fn = f"{sha}.jpg"
    (labeling.LABELING_IMG_DIR).mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
    rid = labeling.insert_candidate(
        cls=cls, image_path=fn, source=source, source_url=None,
        subject_plant=None, sha=sha, phash=None, scan_id=scan_id or sha,
    )
    if rid and status != "pending":
        with labeling._connect() as conn:
            conn.execute("UPDATE candidates SET status=? WHERE id=?", (status, rid))
    return rid


# ── store / concurrency ─────────────────────────────────────────────
def test_claim_is_exclusive_across_sessions(labdb):
    _seed("nutrient_deficiency")
    a = labeling.claim_next("sessA", 5)
    b = labeling.claim_next("sessB", 5)
    assert len(a) == 1 and b == []  # second admin gets nothing


def test_verdict_resolves_only_own_claim(labdb):
    rid = _seed("nutrient_deficiency")
    labeling.claim_next("sessA", 5)
    # a different session cannot resolve A's claim
    assert labeling.record_verdicts("sessB", [(rid, "yes")]) == 0
    assert labeling.record_verdicts("sessA", [(rid, "yes")]) == 1
    assert labeling.stats({"nutrient_deficiency": 600})[
        "per_class"]["nutrient_deficiency"]["validated"] == 1


def test_class_filter_narrows_pool(labdb):
    _seed("gray_mold_botrytis")
    _seed("nutrient_deficiency")
    cards = labeling.claim_next("s", 5, classes=["nutrient_deficiency"])
    assert {c["class"] for c in cards} == {"nutrient_deficiency"}


def test_lease_expiry_requeues(labdb):
    _seed("nutrient_deficiency")
    labeling.claim_next("sessA", 5)
    # age the claim well past the lease
    with labeling._connect() as conn:
        conn.execute("UPDATE candidates SET claimed_at = claimed_at - 10000 "
                     "WHERE status='claimed'")
    assert labeling.requeue_expired_claims(900) >= 1
    assert len(labeling.claim_next("sessB", 5)) == 1


def test_insert_is_sha_idempotent(labdb):
    body = _unique_jpeg()
    sha = hashlib.sha256(body).hexdigest()
    a = labeling.insert_candidate(
        cls="nutrient_deficiency", image_path=f"{sha}.jpg", source="user_scan",
        source_url=None, subject_plant=None, sha=sha, phash=None, scan_id="a1",
    )
    dup = labeling.insert_candidate(
        cls="nutrient_deficiency", image_path=f"{sha}.jpg", source="user_scan",
        source_url=None, subject_plant=None, sha=sha, phash=None, scan_id="a2",
    )
    assert a is not None and dup is None


# ── session auth ────────────────────────────────────────────────────
def test_token_roundtrip_and_tamper(labdb):
    tok, _ = labeling.issue_session_token()
    assert labeling.verify_session_token(tok)
    assert labeling.verify_session_token(tok + "x") is None
    assert labeling.verify_session_token("a.b.c") is None
    assert labeling.verify_session_token(None) is None


def test_blank_password_refuses_login(labdb, monkeypatch):
    monkeypatch.setattr(labeling, "_admin_password", lambda: "")
    assert labeling.verify_admin_password("anything") is False


# ── HTTP endpoints ──────────────────────────────────────────────────
def _login(client) -> str:
    r = client.post("/api/label/login", json={"password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(tok):
    return {"X-Label-Token": tok}


def test_login_bad_password_401(client, labdb):
    assert client.post("/api/label/login", json={"password": "nope"}).status_code == 401


def test_login_throttle_429(client, labdb, monkeypatch):
    monkeypatch.setattr(labeling, "_LOGIN_MAX_ATTEMPTS", 3)
    labeling._login_attempts.clear()
    for _ in range(3):
        client.post("/api/label/login", json={"password": "nope"})
    assert client.post("/api/label/login", json={"password": "s3cret"}).status_code == 429


def test_endpoints_require_session(client, labdb):
    assert client.get("/api/label/next").status_code == 401
    assert client.get("/api/label/stats").status_code == 401


def test_disabled_feature_404(client, labdb, monkeypatch):
    monkeypatch.setattr(server_main, "LABELING_ENABLED", False)
    assert client.post("/api/label/login", json={"password": "s3cret"}).status_code == 404
    tok, _ = labeling.issue_session_token()
    assert client.get("/api/label/stats", headers=_auth(tok)).status_code == 404


def test_full_flow_next_verdict_stats(client, labdb, monkeypatch):
    monkeypatch.setattr(server_main, "LABELING_TARGETS", {"nutrient_deficiency": 600})
    _seed("nutrient_deficiency")
    tok = _login(client)
    nxt = client.get("/api/label/next?count=5", headers=_auth(tok)).json()
    assert len(nxt["cards"]) == 1
    cid = nxt["cards"][0]["id"]
    # serve the image
    assert client.get(f"/api/label/image/{cid}", headers=_auth(tok)).status_code == 200
    v = client.post("/api/label/verdict", headers=_auth(tok),
                    json={"verdicts": [{"id": cid, "verdict": "yes"}]})
    assert v.json()["applied"] == 1
    st = client.get("/api/label/stats", headers=_auth(tok)).json()
    assert st["per_class"]["nutrient_deficiency"]["validated"] == 1


def test_clue_pass_drop_payload(client, labdb):
    tok = _login(client)
    r = client.get("/api/label/clue/leaf_scorch_underwatering", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["suggested"]["class"] == "leaf_scorch_underwatering"
    assert body["pass"] and body["drop"]
    assert any(c["class"] == "sunburn_or_scorch" for c in body["confusables"])


def test_clue_guards(client, labdb):
    tok = _login(client)
    assert client.get("/api/label/clue/bogus", headers=_auth(tok)).status_code == 404
    assert client.get("/api/label/image/99999", headers=_auth(tok)).status_code == 404
