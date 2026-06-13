"""Tests for the candidate-ingestion package (server/ingest/)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

import labeling
from ingest import pipeline as ingest_pipeline
from ingest.imagefilter import is_near_dup, valid_image_bytes


def _jpeg(size=(400, 400), color=(0, 200, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_valid_image_bytes_minside_gate():
    assert valid_image_bytes(_jpeg((400, 400))) is not None
    assert valid_image_bytes(_jpeg((200, 200))) is None     # below 384 floor
    assert valid_image_bytes(b"not an image") is None


def test_near_dup_detection():
    from ingest.imagefilter import phash_bytes
    h = phash_bytes(_jpeg(color=(10, 180, 30)))
    assert h is not None
    assert is_near_dup(h, {h}) is True
    assert is_near_dup(h, set()) is False


@pytest.fixture
def labdb():
    if labeling.LABELING_DB_PATH.exists():
        labeling.LABELING_DB_PATH.unlink()
    labeling._initialised = False
    labeling.init_db()
    return labeling


def _distinct_jpegs(tmp_path, n):
    """n visually distinct images (distinct pHash so dedup keeps them all)."""
    from PIL import ImageDraw
    paths = []
    for i in range(n):
        p = tmp_path / f"c{i}.jpg"
        img = Image.new("RGB", (400, 400), (20, 190, 40))
        d = ImageDraw.Draw(img)
        d.rectangle(
            [20 + i * 90, 20, 120 + i * 110, 200 + i * 50],
            fill=(200, 60 + i * 60, 30),
        )
        img.save(p, format="JPEG")
        paths.append(str(p))
    return paths


def test_run_ingestion_deficit_driven(labdb, tmp_path, monkeypatch):
    # one candidate per class via a stubbed probe (no network / Kaggle)
    def fake_probe(cls, slug, target_classes):
        p = tmp_path / f"{cls}.jpg"
        p.write_bytes(_jpeg(color=(20, 190, 40)))
        yield {"local_path": str(p), "source": "kaggle_supplement",
               "source_url": f"kaggle:test/{cls}/x.jpg", "subject_plant": None}

    monkeypatch.setattr(ingest_pipeline, "probe_class", fake_probe)
    targets = {"nutrient_deficiency": 2, "leaf_spot": 0}  # leaf_spot weight 0 → skipped
    added = ingest_pipeline.run_ingestion(targets, kaggle_slug=None, img_dir=tmp_path / "img")
    assert added.get("nutrient_deficiency") == 1
    assert "leaf_spot" not in added
    # already has a pending candidate → still under target(2) by pending+validated
    st = labeling.stats(targets)["per_class"]["nutrient_deficiency"]
    assert st["pending"] == 1


def test_run_ingestion_auto_labels(labdb, tmp_path, monkeypatch):
    # 3 distinct candidates; the classifier routes them yes/no/pending.
    paths = _distinct_jpegs(tmp_path, 3)

    def fake_probe(cls, slug, target_classes):
        for p in paths:
            yield {"local_path": p, "source": "kaggle_supplement",
                   "source_url": f"kaggle:test/x/{p}", "subject_plant": None}

    routes = iter([
        ("auto_yes", "nutrient_deficiency", 0.9, ["nutrient_deficiency"]),
        ("auto_no", "leaf_spot", 0.95, ["leaf_spot"]),
        ("pending", "nutrient_deficiency", 0.5, ["nutrient_deficiency", "leaf_spot"]),
    ])

    def classify(img, proposed):
        return next(routes)

    monkeypatch.setattr(ingest_pipeline, "probe_class", fake_probe)
    targets = {"nutrient_deficiency": 10}
    ingest_pipeline.run_ingestion(targets, None, tmp_path / "img", classify=classify)
    st = labeling.stats(targets)["per_class"]["nutrient_deficiency"]
    assert st["auto_yes"] == 1 and st["pending"] == 1
    # auto_no rows are KEPT (hard-negative preservation), but don't count
    # toward the target / validated supply.
    assert st["auto_no"] == 1
    assert st["validated"] == 1                          # auto_yes counts as approved


def test_run_ingestion_audit_sampling(labdb, tmp_path, monkeypatch):
    # audit_rate=1.0 → every confident call lands pending with audit=1
    paths = _distinct_jpegs(tmp_path, 2)

    def fake_probe(cls, slug, target_classes):
        for p in paths:
            yield {"local_path": p, "source": "kaggle_supplement",
                   "source_url": f"kaggle:test/x/{p}", "subject_plant": None}

    def classify(img, proposed):
        return ("auto_yes", proposed, 0.95, [proposed])

    monkeypatch.setattr(ingest_pipeline, "probe_class", fake_probe)
    targets = {"nutrient_deficiency": 10}
    ingest_pipeline.run_ingestion(
        targets, None, tmp_path / "img", classify=classify, audit_rate=1.0,
    )
    st = labeling.stats(targets)["per_class"]["nutrient_deficiency"]
    assert st["pending"] == 2 and st["auto_yes"] == 0
    with labeling._connect() as conn:
        audits = conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE audit=1"
        ).fetchone()[0]
    assert audits == 2
    # audit_rate=0 → none re-routed (fresh DB)
    labeling.LABELING_DB_PATH.unlink()
    labeling._initialised = False
    labeling.init_db()
    ingest_pipeline.run_ingestion(
        targets, None, tmp_path / "img2", classify=classify, audit_rate=0.0,
    )
    st = labeling.stats(targets)["per_class"]["nutrient_deficiency"]
    assert st["auto_yes"] == 2 and st["pending"] == 0


def test_run_ingestion_skips_satisfied(labdb, tmp_path, monkeypatch):
    called = {"n": 0}

    def fake_probe(cls, slug, target_classes):
        called["n"] += 1
        return iter(())

    monkeypatch.setattr(ingest_pipeline, "probe_class", fake_probe)
    # target met by zero weight → nothing to do, probe never called
    assert ingest_pipeline.run_ingestion({"healthy": 0}, None, tmp_path) == {}
    assert called["n"] == 0
