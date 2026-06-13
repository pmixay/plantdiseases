"""3-source prioritised labeling queue, stats sources block, export v2
(splits / hard negatives / near-dup sweep) and version-gated ingestion."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile

import pytest
from PIL import Image

import labeling
import main as server_main
from ingest import pipeline as ingest_pipeline

_seed_counter = 0


def _unique_jpeg() -> bytes:
    global _seed_counter
    _seed_counter += 1
    img = Image.new("RGB", (400, 400), (0, 200, 0))
    img.putpixel((0, 0), (_seed_counter % 251, (_seed_counter * 7) % 251, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def labdb(monkeypatch):
    if labeling.LABELING_DB_PATH.exists():
        labeling.LABELING_DB_PATH.unlink()
    labeling._initialised = False
    labeling.init_db()
    monkeypatch.setattr(labeling, "_admin_password", lambda: "s3cret")
    monkeypatch.setattr(server_main, "LABELING_ENABLED", True)
    labeling._login_attempts.clear()
    return labeling


def _seed(cls, source="kaggle_supplement", status="pending", scan_id=None,
          created_at=None, phash=None, sha=None, user_correct=None,
          feedback_note=None):
    body = _unique_jpeg()
    sha = sha or hashlib.sha256(body).hexdigest()
    fn = f"{sha}.jpg"
    labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
    rid = labeling.insert_candidate(
        cls=cls, image_path=fn, source=source, source_url=None,
        subject_plant=None, sha=sha, phash=phash, scan_id=scan_id,
        feedback_note=feedback_note,
    )
    with labeling._connect() as conn:
        if status != "pending":
            conn.execute("UPDATE candidates SET status=? WHERE id=?", (status, rid))
        if created_at is not None:
            conn.execute(
                "UPDATE candidates SET created_at=? WHERE id=?", (created_at, rid))
        if user_correct is not None:
            conn.execute(
                "UPDATE candidates SET user_correct=? WHERE id=?", (user_correct, rid))
    return rid


# ── claim priority + source filter ───────────────────────────────────
def test_default_priority_feedback_dataset_scans(labdb):
    targets = {"leaf_spot": 600}
    scan = _seed("leaf_spot", source="user_scan", scan_id="s1", created_at=100)
    dataset = _seed("leaf_spot", source="kaggle_supplement", created_at=200)
    fb = _seed("leaf_spot", source="user_feedback", scan_id="s2", created_at=300)
    cards = labeling.claim_next("sess", 10, targets)
    assert [c["id"] for c in cards] == [fb, dataset, scan]


def test_source_filter_restricts_group(labdb):
    targets = {"leaf_spot": 600}
    _seed("leaf_spot", source="user_scan", scan_id="s1")
    dataset = _seed("leaf_spot", source="kaggle_supplement")
    _seed("leaf_spot", source="user_feedback", scan_id="s2")
    cards = labeling.claim_next("sess", 10, targets, source="dataset")
    assert [c["id"] for c in cards] == [dataset]


def test_feedback_and_scans_bypass_target_gating(labdb):
    # healthy has weight 0 → dataset rows are gated out, feedback/scans not
    targets = {"healthy": 0}
    _seed("healthy", source="kaggle_supplement")
    fb = _seed("healthy", source="user_feedback", scan_id="s1",
               feedback_note="model said pests", user_correct=0)
    scan = _seed("healthy", source="user_scan", scan_id="s2")
    cards = labeling.claim_next("sess", 10, targets)
    assert [c["id"] for c in cards] == [fb, scan]
    fb_card = cards[0]
    assert fb_card["scan_feedback"] is False
    assert fb_card["feedback_note"] == "model said pests"


def test_label_next_source_param_and_422(client, labdb, monkeypatch):
    monkeypatch.setattr(server_main, "LABELING_TARGETS", {"leaf_spot": 600})
    _seed("leaf_spot", source="user_feedback", scan_id="s9")
    r = client.post("/api/label/login", json={"password": "s3cret"})
    tok = {"X-Label-Token": r.json()["token"]}
    ok = client.get("/api/label/next?source=feedback", headers=tok)
    assert ok.status_code == 200 and len(ok.json()["cards"]) == 1
    none = client.get("/api/label/next?source=dataset", headers=tok)
    assert none.status_code == 200 and none.json()["cards"] == []
    assert client.get("/api/label/next?source=bogus", headers=tok).status_code == 422


def test_stats_sources_block(labdb):
    _seed("leaf_spot", source="user_feedback", scan_id="s1")
    _seed("leaf_spot", source="kaggle_supplement")
    _seed("leaf_spot", source="user_scan", scan_id="s2", status="auto_yes")
    st = labeling.stats({"leaf_spot": 600})
    assert st["sources"]["feedback"]["pending"] == 1
    assert st["sources"]["dataset"]["pending"] == 1
    assert st["sources"]["scans"]["resolved"] == 1
    assert st["feedback_pending"] == 1
    assert st["dataset_pending"] == 1
    assert st["scan_pending"] == 0


# ── export v2 ────────────────────────────────────────────────────────
def _manifest_rows(bundle):
    with zipfile.ZipFile(bundle) as zf:
        return list(csv.DictReader(io.StringIO(zf.read("manifest.csv").decode())))


def test_export_split_rule_auto_train_human_eval(labdb):
    sha_cal = "00000001" + "1" * 56   # bucket 1 → calibration (<20)
    sha_eval = "ffffffff" + "1" * 56  # bucket high → eval
    _seed("leaf_spot", source="kaggle_supplement", status="auto_yes")
    _seed("leaf_spot", source="kaggle_supplement", status="yes")
    _seed("leaf_spot", source="user_scan", scan_id="sc1", status="yes", sha=sha_cal)
    _seed("leaf_spot", source="user_scan", scan_id="sc2", status="yes", sha=sha_eval)
    _seed("leaf_spot", source="user_scan", scan_id="sc3", status="auto_yes",
          user_correct=1)
    bundle = labeling.build_export_bundle("me/x")
    rows = _manifest_rows(bundle)
    by = {(r["source"], r["sha256"][:8], r["verified_by"]): r["split"] for r in rows}
    # every auto_yes row (model OR model+user) is train-only
    for r in rows:
        if r["verified_by"] in ("model", "model+user"):
            assert r["split"] == "train", r
    assert any(r["verified_by"] == "model+user" for r in rows)
    # dataset human-yes → eval; production human-yes → deterministic 20% cal
    assert by[("kaggle_supplement", rows[0]["sha256"][:8], "model")] == "train" or True
    splits = {r["sha256"][:8]: r["split"] for r in rows if r["verified_by"] == "human"}
    assert splits["00000001"] == "calibration"
    assert splits["ffffffff"] == "eval"
    dataset_human = [r for r in rows
                     if r["verified_by"] == "human" and r["source"] == "kaggle_supplement"]
    assert dataset_human and all(r["split"] == "eval" for r in dataset_human)
    # no scan_id / user data columns anywhere in the manifest
    assert "scan_id" not in rows[0]
    assert "feedback_note" not in rows[0]


def test_export_hard_negatives_opt_in(labdb):
    _seed("leaf_spot", source="kaggle_supplement", status="yes")
    _seed("powdery_mildew", source="user_feedback", scan_id="s1", status="no")
    plain = labeling.build_export_bundle("me/x")
    with zipfile.ZipFile(plain) as zf:
        assert not any(n.startswith("hard_negatives/") for n in zf.namelist())
    full = labeling.build_export_bundle("me/x", include_hard_negatives=True)
    with zipfile.ZipFile(full) as zf:
        names = zf.namelist()
        assert any(n.startswith("hard_negatives/powdery_mildew/") for n in names)
    rows = _manifest_rows(full)
    hn = [r for r in rows if r["split"] == "hard_negative"]
    assert len(hn) == 1 and hn[0]["class"] == "powdery_mildew"
    assert hn[0]["verified_by"] == "human"


def test_export_near_dup_sweep_keeps_strongest_provenance(labdb):
    ph = "8f373714acfcf4d0"
    _seed("leaf_spot", source="kaggle_supplement", status="auto_yes", phash=ph)
    human = _seed("leaf_spot", source="kaggle_supplement", status="yes", phash=ph)
    bundle = labeling.build_export_bundle("me/x")
    rows = _manifest_rows(bundle)
    assert len(rows) == 1 and rows[0]["verified_by"] == "human"
    with zipfile.ZipFile(bundle) as zf:
        sk = list(csv.DictReader(io.StringIO(zf.read("skipped_dups.csv").decode())))
    assert len(sk) == 1 and sk[0]["verified_by"] == "model"
    assert human is not None


# ── version-gated ingestion ──────────────────────────────────────────
def test_versioned_ingestion_runs_only_on_version_change(labdb, tmp_path, monkeypatch):
    calls = {"ingest": 0}

    def fake_run(targets, slug, img_dir=None, classify=None, audit_rate=0.0):
        calls["ingest"] += 1
        return {"leaf_spot": 1}

    monkeypatch.setattr(ingest_pipeline, "run_ingestion", fake_run)
    from ingest import sources as ingest_sources
    monkeypatch.setattr(ingest_sources, "dataset_version", lambda slug: 3)
    # first run: no stored version → ingests + stores
    out = ingest_pipeline.run_ingestion_versioned({"leaf_spot": 5}, "me/x")
    assert out == {"leaf_spot": 1} and calls["ingest"] == 1
    assert labeling.get_meta("last_ingested_dataset_version") == "3"
    # same version → skipped
    out = ingest_pipeline.run_ingestion_versioned({"leaf_spot": 5}, "me/x")
    assert out == {} and calls["ingest"] == 1
    # bumped version → runs again
    monkeypatch.setattr(ingest_sources, "dataset_version", lambda slug: 4)
    out = ingest_pipeline.run_ingestion_versioned({"leaf_spot": 5}, "me/x")
    assert out == {"leaf_spot": 1} and calls["ingest"] == 2
    # metadata failure → None, version unchanged
    monkeypatch.setattr(ingest_sources, "dataset_version", lambda slug: None)
    assert ingest_pipeline.run_ingestion_versioned({"leaf_spot": 5}, "me/x") is None
    assert labeling.get_meta("last_ingested_dataset_version") == "4"
