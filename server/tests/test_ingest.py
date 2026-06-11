"""Tests for the candidate-ingestion package (server/ingest/)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

import labeling
from ingest import pipeline as ingest_pipeline
from ingest.disjoint import is_disjoint
from ingest.imagefilter import is_near_dup, valid_image_bytes


def _jpeg(size=(400, 400), color=(0, 200, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_disjointness_rejects_training_sources():
    for bad in [
        "https://www.inaturalist.org/observations/1",
        "https://static.inaturalist.org/photos/x.jpg",
        "https://www.kaggle.com/datasets/abdulahad0296/indoor",
        "https://example.com/plantvillage/leaf.jpg",
        "https://upload.wikimedia.org/x.jpg",
        "https://en.wikipedia.org/wiki/Botrytis",
        "kaggle:usmanafzaal/strawberry/gm.jpg",
    ]:
        assert is_disjoint(bad) is False, bad
    for ok in [
        "https://extension.university.edu/chlorosis.jpg",
        "https://live.staticflickr.com/123/x.jpg",
        "kaggle:maxayyt5/houseplant-disease-eval-supplement/nd_1.jpg",
        None,
    ]:
        assert is_disjoint(ok) is True, ok


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


def test_run_ingestion_deficit_driven(labdb, tmp_path, monkeypatch):
    # one green candidate per class via a stubbed probe (no network)
    def fake_probe(cls, slug, target_classes):
        p = tmp_path / f"{cls}.jpg"
        p.write_bytes(_jpeg(color=(20, 190, 40)))
        yield {"local_path": str(p), "source": "openverse",
               "source_url": "https://example.com/x", "subject_plant": None}

    monkeypatch.setattr(ingest_pipeline, "probe_class", fake_probe)
    targets = {"nutrient_deficiency": 2, "leaf_spot": 0}  # leaf_spot weight 0 → skipped
    added = ingest_pipeline.run_ingestion(targets, kaggle_slug=None, img_dir=tmp_path / "img")
    assert added.get("nutrient_deficiency") == 1
    assert "leaf_spot" not in added
    # already has a pending candidate → still under target(2) by pending+validated
    st = labeling.stats(targets)["per_class"]["nutrient_deficiency"]
    assert st["pending"] == 1


def test_run_ingestion_skips_satisfied(labdb, tmp_path, monkeypatch):
    called = {"n": 0}

    def fake_probe(cls, slug, target_classes):
        called["n"] += 1
        return iter(())

    monkeypatch.setattr(ingest_pipeline, "probe_class", fake_probe)
    # target met by zero weight → nothing to do, probe never called
    assert ingest_pipeline.run_ingestion({"healthy": 0}, None, tmp_path) == {}
    assert called["n"] == 0
