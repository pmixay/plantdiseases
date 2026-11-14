"""Privacy and data-quality contract for the offline training export."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
import tarfile
from contextlib import closing
from datetime import UTC, datetime

import pytest
from PIL import Image, ImageDraw

import training_store
from tools import export_training_data as exporter


@pytest.fixture
def source_store(tmp_path, monkeypatch):
    monkeypatch.setattr(training_store, "TRAINING_DB_PATH", tmp_path / "training.db")
    monkeypatch.setattr(training_store, "TRAINING_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(training_store, "_initialised", False)
    training_store.init_db()
    return training_store


def _insert(
    store, colour: tuple[int, int, int] | Image.Image, class_name: str
) -> str:
    source = (
        colour.copy()
        if isinstance(colour, Image.Image)
        else Image.new("RGB", (160, 120), colour)
    )
    exif = Image.Exif()
    exif[271] = "SENTINEL-CAMERA-MAKE"
    raw = io.BytesIO()
    source.save(raw, format="JPEG", exif=exif, comment=b"SENTINEL-XMP")
    image = store.sanitize_training_image(raw.getvalue())
    sample_id, created = store.store_sample(
        image,
        source="consented_scan",
        model_family="gamma",
        model_version="2.6.9",
        predicted_class=class_name,
        predicted_confidence=0.88,
        top_k=[{"class": class_name, "confidence": 0.88}],
        conformal_set=[class_name],
        warnings=[],
        blur_bucket="sharp",
        detector_region_count=1,
        bbox_area_buckets=["large"],
        severity="medium",
        review_status="auto_yes",
        consent_version="model-improvement-v1",
    )
    assert created
    return sample_id


def _members(path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as archive:
        return {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        }


def test_export_is_read_only_privacy_checked_and_group_locked(
    source_store, tmp_path
):
    base = Image.new("RGB", (160, 120))
    pixels = base.load()
    for y in range(120):
        for x in range(160):
            pixels[x, y] = ((x * 3 + y) % 256, (y * 2 + x) % 256, (x + y * 2) % 256)
    variant = base.copy()
    ImageDraw.Draw(variant).rectangle((10, 10, 18, 18), fill=(20, 140, 80))
    first = _insert(source_store, base, "leaf_spot")
    second = _insert(source_store, variant, "healthy")
    archive = exporter.export_training_data(
        source_store.TRAINING_DB_PATH,
        source_store.TRAINING_IMAGE_DIR,
        tmp_path / "exports",
        now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )

    assert archive.name == "training-export-20260807T120000Z.tar.gz"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o444
    members = _members(archive)
    assert {
        "metadata.json",
        "manifest.jsonl",
        "inventory.json",
        "duplicates.json",
        "checksums.sha256",
    } <= set(members)
    images = {name: body for name, body in members.items() if name.startswith("images/")}
    assert len(images) == 2
    assert all(source_store.metadata_is_absent(body) for body in images.values())

    manifest = [
        json.loads(line) for line in members["manifest.jsonl"].splitlines()
    ]
    assert {row["sample_id"] for row in manifest} == {first, second}
    assert {row["offline_usage"] for row in manifest} == {"train_only"}
    # Uniform-colour fixtures have the same pHash despite distinct sanitized
    # SHA values, so they must remain in one offline split.
    assert len({row["sha256"] for row in manifest}) == 2
    assert len({row["duplicate_group"] for row in manifest}) == 1

    inventory = json.loads(members["inventory.json"])
    assert inventory["total"] == 2
    assert inventory["by_source"] == {"consented_scan": 2}
    assert inventory["by_model_version"] == {"2.6.9": 2}
    duplicates = json.loads(members["duplicates.json"])
    assert len(duplicates["near_groups"]) == 1

    checksums = {}
    for line in members["checksums.sha256"].decode("ascii").splitlines():
        digest, name = line.split("  ", 1)
        checksums[name] = digest
    assert set(checksums) == set(members) - {"checksums.sha256"}
    assert all(hashlib.sha256(members[name]).hexdigest() == digest for name, digest in checksums.items())

    archive_text = b"\n".join(
        body for name, body in members.items() if not name.startswith("images/")
    ).lower()
    for forbidden in (
        b"feedback_receipt",
        b"receipt_hash",
        b"user_pseudo_id",
        b"community_actor_id",
        b"client_ip",
        b"nickname",
        b"original_filename",
        b"sentinel-camera-make",
        b"sentinel-xmp",
    ):
        assert forbidden not in archive_text
    assert all(b"SENTINEL" not in body for body in images.values())


def test_export_refuses_hash_mismatch_without_leaving_archive(source_store, tmp_path):
    _insert(source_store, (100, 20, 20), "leaf_spot")
    image_path = next(source_store.TRAINING_IMAGE_DIR.iterdir())
    image_path.write_bytes(b"tampered")
    output = tmp_path / "exports"
    with pytest.raises(exporter.ExportError, match="checksum mismatch"):
        exporter.export_training_data(
            source_store.TRAINING_DB_PATH, source_store.TRAINING_IMAGE_DIR, output
        )
    assert list(output.iterdir()) == []


def test_export_refuses_metadata_even_when_checksum_matches(source_store, tmp_path):
    _insert(source_store, (100, 30, 20), "leaf_spot")
    image_path = next(source_store.TRAINING_IMAGE_DIR.iterdir())
    exif = Image.Exif()
    exif[271] = "SENTINEL-CAMERA-MAKE"
    raw = io.BytesIO()
    Image.new("RGB", (100, 100), "red").save(raw, format="JPEG", exif=exif)
    unsafe = raw.getvalue()
    image_path.write_bytes(unsafe)
    with closing(sqlite3.connect(source_store.TRAINING_DB_PATH)) as connection, connection:
        connection.execute(
            "UPDATE training_samples SET sha256 = ?",
            (hashlib.sha256(unsafe).hexdigest(),),
        )
    with pytest.raises(exporter.ExportError, match="contains metadata"):
        exporter.export_training_data(
            source_store.TRAINING_DB_PATH,
            source_store.TRAINING_IMAGE_DIR,
            tmp_path / "exports",
        )


def test_export_requires_explicit_valid_sources(tmp_path):
    with pytest.raises(exporter.ExportError, match="database does not exist"):
        exporter.export_training_data(
            tmp_path / "missing.db", tmp_path, tmp_path / "exports"
        )
