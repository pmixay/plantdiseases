"""Tests for the human data-labeling backend (server/labeling.py + endpoints)."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from PIL import Image

import labeling
import main as server_main

_seed_counter = 0


def _jpeg(color=(0, 200, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), color).save(buf, format="JPEG")
    return buf.getvalue()


def _unique_jpeg() -> bytes:
    """Distinct bytes each call so the SHA-unique index doesn't collapse seeds."""
    global _seed_counter
    _seed_counter += 1
    img = Image.new("RGB", (400, 400), (0, 200, 0))
    img.putpixel((0, 0), (_seed_counter % 251, (_seed_counter * 7) % 251, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def labdb(monkeypatch):
    """Fresh labeling DB + a known admin password, feature enabled."""
    if labeling.LABELING_DB_PATH.exists():
        labeling.LABELING_DB_PATH.unlink()
    labeling._initialised = False
    labeling.init_db()
    monkeypatch.setattr(labeling, "_admin_password", lambda: "s3cret")
    monkeypatch.setattr(server_main, "LABELING_ENABLED", True)
    labeling._login_attempts.clear()
    return labeling


def _seed(cls, status="pending", color=None):
    body = _unique_jpeg()
    sha = hashlib.sha256(body).hexdigest()
    fn = f"{sha}.jpg"
    (labeling.LABELING_IMG_DIR).mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
    rid = labeling.insert_candidate(
        cls=cls, image_path=fn, source="test", source_url="https://example.com/x",
        subject_plant="pothos", sha=sha, phash=None,
    )
    if rid and status != "pending":
        with labeling._connect() as conn:
            conn.execute("UPDATE candidates SET status=? WHERE id=?", (status, rid))
    return rid


# ── store / concurrency ─────────────────────────────────────────────
def test_claim_is_exclusive_across_sessions(labdb):
    _seed("nutrient_deficiency")
    targets = {"nutrient_deficiency": 600}
    a = labeling.claim_next("sessA", 5, targets)
    b = labeling.claim_next("sessB", 5, targets)
    assert len(a) == 1 and b == []  # second admin gets nothing


def test_verdict_resolves_only_own_claim(labdb):
    rid = _seed("nutrient_deficiency")
    targets = {"nutrient_deficiency": 600}
    labeling.claim_next("sessA", 5, targets)
    # a different session cannot resolve A's claim
    assert labeling.record_verdicts("sessB", [(rid, "yes")]) == 0
    assert labeling.record_verdicts("sessA", [(rid, "yes")]) == 1
    assert labeling.stats(targets)["per_class"]["nutrient_deficiency"]["validated"] == 1


def test_satisfied_class_yields_nothing(labdb):
    _seed("gray_mold_botrytis", status="yes")   # validated = 1
    _seed("gray_mold_botrytis", status="pending")
    targets = {"gray_mold_botrytis": 1}          # already satisfied
    assert labeling.claim_next("s", 5, targets) == []


def test_random_mode_skips_satisfied(labdb):
    _seed("gray_mold_botrytis", status="yes")
    _seed("nutrient_deficiency", status="pending")
    targets = {"gray_mold_botrytis": 1, "nutrient_deficiency": 600}
    cards = labeling.claim_next("s", 5, targets)  # no class filter = random/any
    assert {c["class"] for c in cards} == {"nutrient_deficiency"}


def test_lease_expiry_requeues(labdb):
    _seed("nutrient_deficiency")
    targets = {"nutrient_deficiency": 600}
    labeling.claim_next("sessA", 5, targets)
    # age the claim well past the lease
    with labeling._connect() as conn:
        conn.execute("UPDATE candidates SET claimed_at = claimed_at - 10000 "
                     "WHERE status='claimed'")
    assert labeling.requeue_expired_claims(900) >= 1
    assert len(labeling.claim_next("sessB", 5, targets)) == 1


def test_insert_is_sha_idempotent(labdb):
    body = _unique_jpeg()
    sha = hashlib.sha256(body).hexdigest()
    a = labeling.insert_candidate(
        cls="nutrient_deficiency", image_path=f"{sha}.jpg", source="t",
        source_url=None, subject_plant=None, sha=sha, phash=None,
    )
    dup = labeling.insert_candidate(
        cls="nutrient_deficiency", image_path=f"{sha}.jpg", source="t",
        source_url=None, subject_plant=None, sha=sha, phash=None,
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


# ── export bundle ───────────────────────────────────────────────────
def test_export_bundle_layout(labdb):
    _seed("nutrient_deficiency", status="yes")
    _seed("gray_mold_botrytis", status="yes", color=(120, 90, 90))
    bundle = labeling.build_export_bundle("me/houseplant-disease-eval-supplement")
    assert bundle is not None and bundle.is_file()
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        assert any(n.startswith("nutrient_deficiency/nd_") for n in names)
        assert any(n.startswith("gray_mold_botrytis/gm_") for n in names)
        assert "manifest.csv" in names and "dataset-metadata.json" in names
        manifest = zf.read("manifest.csv").decode()
        assert "human" in manifest  # verified_by = human (status 'yes')


def test_export_none_when_empty(labdb):
    assert labeling.build_export_bundle("me/x") is None


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


def test_rescore_routes_pending(client, labdb):
    # FakePipeline predicts 'healthy' at low conf, so a nutrient candidate stays
    # pending (model not confident) — the endpoint runs and reports counts.
    _seed("nutrient_deficiency")
    tok = _login(client)
    r = client.post("/api/label/rescore?limit=50", headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert "rescored" in r.json()


def test_clue_guards(client, labdb):
    tok = _login(client)
    assert client.get("/api/label/clue/bogus", headers=_auth(tok)).status_code == 404
    assert client.get("/api/label/image/99999", headers=_auth(tok)).status_code == 404


def test_export_endpoint_and_download(client, labdb):
    _seed("nutrient_deficiency", status="yes")
    tok = _login(client)
    r = client.post("/api/label/export", headers=_auth(tok))
    assert r.status_code == 200, r.text
    name = r.json()["bundle"]
    assert "kaggle datasets version" in r.json()["kaggle_command"]
    dl = client.get(f"/api/label/export/{name}", headers=_auth(tok))
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    # path traversal guard
    assert client.get("/api/label/export/..%2f..%2fmain.py", headers=_auth(tok)).status_code in (400, 404)
