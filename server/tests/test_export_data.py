"""Offline data-export CLI (server/tools/export_data.py)."""

from __future__ import annotations

import hashlib
import json

import labeling
from tools import export_data


def _seed_scan(scan_id: str, cls: str = "gray_mold_botrytis") -> None:
    body = f"img-{scan_id}".encode()
    sha = hashlib.sha256(body).hexdigest()
    labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / f"{sha}.jpg").write_bytes(body)
    labeling.insert_candidate(
        cls=cls, image_path=f"{sha}.jpg", source="user_scan", source_url=None,
        subject_plant=None, sha=sha, phash=None, status="pending",
        model_pred=cls, model_conf=0.6, scan_id=scan_id,
    )


def test_export_bundles_rows_and_images(monkeypatch, tmp_path):
    monkeypatch.setattr(labeling, "LABELING_DB_PATH", tmp_path / "labeling.db")
    monkeypatch.setattr(labeling, "LABELING_IMG_DIR", tmp_path / "labeling_images")
    labeling._initialised = False
    labeling.init_db()
    _seed_scan("s1")
    _seed_scan("s2", cls="nutrient_deficiency")

    staging = tmp_path / "out"
    manifest = export_data.export(staging, include_images=True)

    scans = manifest["stores"]["scans"]
    assert scans["present"] is True
    assert scans["tables"]["candidates"] == 2
    assert scans["images"] == 2

    # JSONL is one judged-offline row per scan, model_pred carried as weak prior
    lines = (staging / "scans" / "candidates.jsonl").read_text().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["source"] == "user_scan" and row["model_pred"] == row["class"]
    assert (staging / "manifest.json").is_file()


def test_export_no_images_skips_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(labeling, "LABELING_DB_PATH", tmp_path / "labeling.db")
    monkeypatch.setattr(labeling, "LABELING_IMG_DIR", tmp_path / "labeling_images")
    labeling._initialised = False
    labeling.init_db()
    _seed_scan("s1")

    staging = tmp_path / "out"
    manifest = export_data.export(staging, include_images=False)
    assert manifest["stores"]["scans"]["tables"]["candidates"] == 1
    assert manifest["stores"]["scans"]["images"] == 0
    assert not (staging / "scans" / "images").exists()
