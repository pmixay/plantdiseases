"""Privacy and data-quality contract for the one training export bundle."""

from __future__ import annotations

import hashlib
import io
import json
import random
import sqlite3
import stat
import tarfile
from contextlib import closing
from datetime import UTC, datetime

import pytest
from PIL import Image, ImageDraw

import training_store
from tests.test_community_achievements import _actor, ach, forum, labels  # noqa: F401
from tools import export_training_bundle as exporter

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def source_store(tmp_path, monkeypatch):
    monkeypatch.setattr(training_store, "TRAINING_DB_PATH", tmp_path / "training.db")
    monkeypatch.setattr(training_store, "TRAINING_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(training_store, "_initialised", False)
    training_store.init_db()
    return training_store


def _textured(seed: int) -> Image.Image:
    """A photo whose perceptual hash differs per seed (solid colours all share one)."""
    rng = random.Random(seed)
    image = Image.new("RGB", (16, 12))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(16 * 12)])
    return image.resize((160, 120), Image.Resampling.NEAREST)


def _jpeg_with_metadata(image: Image.Image) -> bytes:
    exif = Image.Exif()
    exif[271] = "SENTINEL-CAMERA-MAKE"
    raw = io.BytesIO()
    image.save(raw, format="JPEG", exif=exif, comment=b"SENTINEL-XMP")
    return raw.getvalue()


def _insert(store, source: Image.Image, class_name: str) -> str:
    image = store.sanitize_training_image(_jpeg_with_metadata(source))
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


def _training(store):
    return (store.TRAINING_DB_PATH, store.TRAINING_IMAGE_DIR)


def _community(forum_module):  # noqa: F811 - the fixture's module
    import community

    return (community.COMMUNITY_DB_PATH, forum_module.media_dir())


def _members(path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as archive:
        return {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        }


def _manifest(members: dict[str, bytes]) -> list[dict]:
    return [json.loads(line) for line in members["manifest.jsonl"].splitlines()]


def _text(members: dict[str, bytes]) -> bytes:
    return b"\n".join(
        body for name, body in members.items() if not name.startswith("images/")
    ).lower()


# ── training store ──────────────────────────────────────────────────────


def test_training_export_is_read_only_privacy_checked_and_family_locked(
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
    archive = exporter.export_bundle(
        tmp_path / "exports", training=_training(source_store), community=None, now=NOW
    )

    assert archive.name == "training-bundle-20260807T120000Z.tar.gz"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o444
    members = _members(archive)
    assert {
        "metadata.json",
        "manifest.jsonl",
        "inventory.json",
        "families.json",
        "checksums.sha256",
    } <= set(members)
    images = {name: body for name, body in members.items() if name.startswith("images/")}
    assert len(images) == 2
    assert all(training_store.metadata_is_absent(body) for body in images.values())

    manifest = _manifest(members)
    assert {row["sample_id"] for row in manifest} == {first, second}
    assert {row["origin"] for row in manifest} == {"training_store"}
    assert {row["offline_usage"] for row in manifest} == {"train_only"}
    assert {row["hints"]["model"]["class"] for row in manifest} == {"leaf_spot", "healthy"}
    assert all(row["hints"]["user_feedback"] is None for row in manifest)
    assert {row["training_store"]["source"] for row in manifest} == {"consented_scan"}
    # Near-identical crops keep distinct SHA values but share a family.
    assert len({row["sha256"] for row in manifest}) == 2
    assert len({row["capture_family"] for row in manifest}) == 1
    assert len(json.loads(members["families.json"])["families"]) == 1

    metadata = json.loads(members["metadata.json"])
    assert metadata["stores"] == ["training_store"]
    assert metadata["label_policy"] == "hints_only_until_offline_review"
    inventory = json.loads(members["inventory.json"])
    assert inventory["total"] == 2
    assert inventory["by_origin"] == {"training_store": 2}

    checksums = {}
    for line in members["checksums.sha256"].decode("ascii").splitlines():
        digest, name = line.split("  ", 1)
        checksums[name] = digest
    assert set(checksums) == set(members) - {"checksums.sha256"}
    assert all(
        hashlib.sha256(members[name]).hexdigest() == digest
        for name, digest in checksums.items()
    )

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
        assert forbidden not in _text(members)
    assert all(b"SENTINEL" not in body for body in images.values())


def test_export_refuses_hash_mismatch_without_leaving_archive(source_store, tmp_path):
    _insert(source_store, _textured(1), "leaf_spot")
    next(source_store.TRAINING_IMAGE_DIR.iterdir()).write_bytes(b"tampered")
    output = tmp_path / "exports"
    with pytest.raises(exporter.ExportError, match="checksum mismatch"):
        exporter.export_bundle(output, training=_training(source_store), community=None)
    assert list(output.iterdir()) == []


def test_export_refuses_metadata_even_when_checksum_matches(source_store, tmp_path):
    _insert(source_store, _textured(2), "leaf_spot")
    image_path = next(source_store.TRAINING_IMAGE_DIR.iterdir())
    unsafe = _jpeg_with_metadata(Image.new("RGB", (100, 100), "red"))
    image_path.write_bytes(unsafe)
    with closing(sqlite3.connect(source_store.TRAINING_DB_PATH)) as connection, connection:
        connection.execute(
            "UPDATE training_samples SET sha256 = ?", (hashlib.sha256(unsafe).hexdigest(),)
        )
    with pytest.raises(exporter.ExportError, match="contains metadata"):
        exporter.export_bundle(
            tmp_path / "exports", training=_training(source_store), community=None
        )


def test_export_requires_explicit_valid_sources(tmp_path):
    with pytest.raises(exporter.ExportError, match="training database does not exist"):
        exporter.export_bundle(
            tmp_path / "exports", training=(tmp_path / "missing.db", tmp_path), community=None
        )
    with pytest.raises(exporter.ExportError, match="community database does not exist"):
        exporter.export_bundle(
            tmp_path / "exports", training=None, community=(tmp_path / "missing.db", tmp_path)
        )
    with pytest.raises(exporter.ExportError, match="no store"):
        exporter.export_bundle(tmp_path / "exports", training=None, community=None)


# ── forum ───────────────────────────────────────────────────────────────


def _post(forum, labels, asker, *, consent, seed, labelled=True):  # noqa: F811
    """A post with one photo and three answers with photos; x1 consents."""
    media = forum.store_media(
        actor_id=_actor(asker), image_bytes=_jpeg_with_metadata(_textured(seed))
    )["media_id"]
    post = forum.create_post(
        actor_id=_actor(asker), nickname="SENTINEL-NICK", kind="question",
        title="SENTINEL-TITLE", body="SENTINEL-BODY", condition="leaf_spot",
        media_ids=[media], training_consent=consent,
    )
    helpers = ("x1", "x2", "x3") if labelled else ("x1",)
    for offset, helper in enumerate(helpers):
        answer_media = forum.store_media(
            actor_id=_actor(helper), image_bytes=_jpeg_with_metadata(_textured(seed * 10 + offset + 1))
        )["media_id"]
        forum.create_answer(
            actor_id=_actor(helper), nickname=helper, post_id=post["id"], body="SENTINEL-ANSWER",
            diagnosis="pests", media_ids=[answer_media], training_consent=helper == "x1",
        )
    labels.refresh()
    return post


def test_forum_export_takes_every_consented_photo_and_nothing_else(
    forum, labels, tmp_path  # noqa: F811
):
    import community
    import community_moderation

    consented = _post(forum, labels, "yes", consent=True, seed=1)
    _post(forum, labels, "no", consent=False, seed=2)
    excluded = _post(forum, labels, "excluded", consent=True, seed=3)
    unlabelled = _post(forum, labels, "fresh", consent=True, seed=4, labelled=False)
    _post(forum, labels, "gone", consent=True, seed=5)
    community_moderation.init_db()
    community_moderation.exclude_from_training(
        admin_actor_id=_actor("admin"), post_id=excluded["id"]
    )
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        connection.execute(
            "INSERT INTO community_deleted_actors (actor_id, requested_at) VALUES (?, 0)",
            (_actor("gone"),),
        )

    archive = exporter.export_bundle(
        tmp_path / "exports", training=None, community=_community(forum), now=NOW
    )
    members = _members(archive)
    manifest = _manifest(members)

    # Post photos: "yes" and the unlabelled "fresh". Answer photos: x1's
    # consented one under each visible post, "no" included (x1 consented
    # even though the asker did not), but not under "excluded" or "gone".
    roles = sorted(row["forum"]["photo_role"] for row in manifest)
    assert roles == ["answer", "answer", "answer", "post", "post"]
    assert {row["origin"] for row in manifest} == {"forum"}
    assert {row["offline_usage"] for row in manifest} == {"train_only"}
    images = {name for name in members if name.startswith("images/")}
    assert images == {row["image"] for row in manifest}
    assert all(training_store.metadata_is_absent(members[name]) for name in images)

    hints = [row["hints"] for row in manifest]
    assert {h["author_condition"] for h in hints} == {"leaf_spot"}
    assert {h["answer_diagnosis"] for h in hints} == {None, "pests"}
    labelled = [h["community"] for h in hints if h["community"]]
    assert labelled and {c["class"] for c in labelled} == {"pests"}
    assert any(h["community"] is None for h in hints)  # "fresh" has no consensus yet

    # One family per plant: the post photo and its answer photo share one.
    by_family: dict[str, list[str]] = {}
    for row in manifest:
        by_family.setdefault(row["capture_family"], []).append(row["forum"]["photo_role"])
    assert sorted(len(v) for v in by_family.values()) == [1, 2, 2]

    text = _text(members)
    for leaked in (consented["id"], unlabelled["id"], _actor("yes"), _actor("x1")):
        assert leaked.lower().encode() not in text
    for sentinel in (b"sentinel", b"nickname", b"plant_group", b"author_token"):
        assert sentinel not in text
    assert json.loads(members["metadata.json"])["missing_forum_files"] == 0


def test_forum_export_skips_and_counts_a_missing_photo(forum, labels, tmp_path):  # noqa: F811
    _post(forum, labels, "yes", consent=True, seed=6, labelled=False)
    next(forum.media_dir().iterdir()).unlink()

    members = _members(
        exporter.export_bundle(tmp_path / "exports", training=None, community=_community(forum))
    )

    assert json.loads(members["metadata.json"])["missing_forum_files"] == 1
    assert len(_manifest(members)) == 1


# ── both stores ─────────────────────────────────────────────────────────


def test_one_archive_holds_both_stores(source_store, forum, labels, tmp_path):  # noqa: F811
    _insert(source_store, _textured(7), "healthy")
    _post(forum, labels, "yes", consent=True, seed=8)

    members = _members(
        exporter.export_bundle(
            tmp_path / "exports",
            training=_training(source_store),
            community=_community(forum),
            now=NOW,
        )
    )
    manifest = _manifest(members)

    assert json.loads(members["metadata.json"])["stores"] == ["forum", "training_store"]
    assert json.loads(members["inventory.json"])["by_origin"] == {
        "forum": 2,
        "training_store": 1,
    }
    assert len({row["sample_id"] for row in manifest}) == 3
    assert len({row["image"] for row in manifest}) == 3
