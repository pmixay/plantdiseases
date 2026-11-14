"""Synthetic-only legacy migration privacy gate."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from contextlib import closing
from datetime import UTC, datetime

import pytest
from PIL import Image

import training_store
from tools import export_training_data
from tools import migrate_legacy_training_data as migration


def _legacy_image(colour: str, sentinel: str) -> bytes:
    exif = Image.Exif()
    exif[271] = sentinel
    exif[272] = "SENTINEL-DEVICE-MODEL"
    body = io.BytesIO()
    Image.new("RGB", (140, 110), colour).save(
        body,
        format="JPEG",
        exif=exif,
        comment=b"SENTINEL-XMP-IPTC",
    )
    return body.getvalue()


@pytest.fixture
def legacy_material(tmp_path):
    labeling_db = tmp_path / "legacy-labeling.db"
    feedback_db = tmp_path / "legacy-feedback.db"
    labeling_images = tmp_path / "legacy-labeling-images"
    feedback_images = tmp_path / "legacy-feedback-images"
    labeling_images.mkdir()
    feedback_images.mkdir()
    (labeling_images / "SENTINEL-original-labeling.jpg").write_bytes(
        _legacy_image("green", "SENTINEL-CAMERA-MAKE-LABELING")
    )
    (feedback_images / "SENTINEL-original-feedback.jpg").write_bytes(
        _legacy_image("red", "SENTINEL-CAMERA-MAKE-FEEDBACK")
    )
    (feedback_images / "unapproved.jpg").write_bytes(
        _legacy_image("blue", "SENTINEL-UNAPPROVED-CAMERA")
    )

    with closing(sqlite3.connect(labeling_db)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE candidates (
                id INTEGER PRIMARY KEY,
                class TEXT, image_path TEXT, source TEXT, source_url TEXT,
                subject_plant TEXT, sha TEXT, phash TEXT, status TEXT,
                claimed_by_session TEXT, claimed_at INTEGER, created_at INTEGER,
                model_pred TEXT, model_conf REAL, model_set TEXT, scan_id TEXT,
                feedback_note TEXT, user_correct INTEGER, audit INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO candidates VALUES "
            "(1, ?, ?, 'user_scan', ?, ?, 'legacy-sha', 'legacy-phash', "
            "'auto_yes', ?, 1700000000, 1700000001, ?, 0.91, ?, ?, ?, NULL, NULL)",
            (
                "healthy",
                "SENTINEL-original-labeling.jpg",
                "https://SENTINEL-source.invalid/private",
                "SENTINEL-SUBJECT-NAME",
                "SENTINEL-CLAIMED-SESSION",
                "healthy",
                json.dumps(["healthy"]),
                "SENTINEL-SCAN-JOIN-1",
                "SENTINEL-FREE-TEXT-NOTE",
            ),
        )

    with closing(sqlite3.connect(feedback_db)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE feedback (
                id INTEGER PRIMARY KEY,
                scan_id TEXT, correct INTEGER, corrected_class TEXT, note TEXT,
                client_ip TEXT, client_ip_hash TEXT, created_at INTEGER,
                predicted_class TEXT, predicted_confidence REAL, top3_json TEXT,
                model_version TEXT, app_version TEXT, locale TEXT, platform TEXT,
                user_pseudo_id TEXT, region_count INTEGER,
                primary_region_index INTEGER, feedback_latency_ms INTEGER,
                reason TEXT, image_hash TEXT, out_of_head INTEGER,
                prediction_set_json TEXT, image_path TEXT, device_model TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO feedback VALUES "
            "(10, ?, 0, 'leaf_spot', ?, ?, ?, 1700000100, 'healthy', 0.91, ?, "
            "'2.6.8', 'SENTINEL-APP', 'en', 'android', ?, 1, 0, 1000, "
            "'wrong_class', 'SENTINEL-IMAGE-HASH', 0, ?, NULL, ?)",
            (
                "SENTINEL-SCAN-JOIN-1",
                "SENTINEL-FREE-TEXT-FEEDBACK",
                "203.0.113.99",
                "SENTINEL-IP-HASH",
                json.dumps([{"class": "healthy", "prob": 0.91}]),
                "SENTINEL-INSTALLATION-ID",
                json.dumps(["healthy", "leaf_spot"]),
                "SENTINEL-PHONE-MODEL",
            ),
        )
        connection.execute(
            "INSERT INTO feedback VALUES "
            "(11, 'SENTINEL-SCAN-STANDALONE', 0, 'edema', ?, ?, ?, 1700000200, "
            "'leaf_spot', 0.88, ?, '2.6.8', NULL, NULL, NULL, ?, 0, NULL, NULL, "
            "'wrong_class', NULL, 1, ?, ?, ?)",
            (
                "SENTINEL-STANDALONE-NOTE",
                "198.51.100.44",
                "SENTINEL-IP-HASH-2",
                json.dumps([{"class": "leaf_spot", "prob": 0.88}]),
                "SENTINEL-INSTALLATION-ID-2",
                json.dumps(["leaf_spot"]),
                "SENTINEL-original-feedback.jpg",
                "SENTINEL-PHONE-MODEL-2",
            ),
        )
        connection.execute(
            "INSERT INTO feedback VALUES "
            "(12, 'SENTINEL-UNAPPROVED-SCAN', 0, 'viral_disease', ?, ?, ?, "
            "1700000300, 'healthy', 0.99, '[]', '2.6.8', NULL, NULL, NULL, ?, "
            "0, NULL, NULL, 'wrong_class', NULL, 0, '[]', 'unapproved.jpg', ?)",
            (
                "SENTINEL-UNAPPROVED-NOTE",
                "192.0.2.1",
                "SENTINEL-UNAPPROVED-IP-HASH",
                "SENTINEL-UNAPPROVED-INSTALLATION",
                "SENTINEL-UNAPPROVED-DEVICE",
            ),
        )

    approval = tmp_path / "approved.json"
    approval.write_text(
        json.dumps(
            {"labeling_candidate_ids": [1], "feedback_row_ids": [10, 11]}
        ),
        encoding="utf-8",
    )
    return migration.MigrationPaths(
        labeling_database=labeling_db,
        labeling_images=labeling_images,
        feedback_database=feedback_db,
        feedback_images=feedback_images,
        approval_file=approval,
        destination_database=tmp_path / "destination" / "training.db",
        destination_images=tmp_path / "destination" / "images",
    )


def test_dry_run_is_default_and_creates_nothing(legacy_material, capsys):
    paths = legacy_material
    result = migration.main(
        [
            "--legacy-labeling-db",
            str(paths.labeling_database),
            "--legacy-labeling-images",
            str(paths.labeling_images),
            "--legacy-feedback-db",
            str(paths.feedback_database),
            "--legacy-feedback-images",
            str(paths.feedback_images),
            "--approved-ids",
            str(paths.approval_file),
            "--destination-db",
            str(paths.destination_database),
            "--destination-images",
            str(paths.destination_images),
            "--consent-version",
            "legacy-reviewed-v1",
        ]
    )
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "mode": "dry_run",
        "approved_labeling_rows": 1,
        "approved_feedback_rows": 2,
        "in_memory_joins": 1,
        "standalone_feedback_images": 1,
        "potential_samples": 2,
    }
    assert not paths.destination_database.exists()
    assert not paths.destination_images.exists()


def test_apply_removes_legacy_linkage_and_exports_cleanly(legacy_material, tmp_path):
    paths = legacy_material
    report = migration.apply(paths, consent_version="legacy-reviewed-v1")
    assert report == {
        "created_samples": 2,
        "deduplicated_samples": 0,
        "in_memory_joins": 1,
        "standalone_feedback_images": 1,
    }
    with closing(sqlite3.connect(paths.destination_database)) as connection, connection:
        rows = connection.execute(
            "SELECT source, predicted_class, user_correct, corrected_class, "
            "out_of_head, review_status, label_provenance, consent_version "
            "FROM training_samples ORDER BY corrected_class"
        ).fetchall()
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM feedback_receipts"
        ).fetchone()[0]
        schema = "\n".join(
            str(row[0])
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        ).lower()
    assert rows == [
        (
            "feedback_upload",
            "leaf_spot",
            0,
            "edema",
            1,
            "pending",
            "model+user",
            "legacy-reviewed-v1",
        ),
        (
            "feedback_upload",
            "healthy",
            0,
            "leaf_spot",
            0,
            "pending",
            "model+user",
            "legacy-reviewed-v1",
        ),
    ]
    assert receipt_count == 0
    assert "scan_id" not in schema
    assert "user_pseudo_id" not in schema
    assert all(
        training_store.metadata_is_absent(image.read_bytes())
        for image in paths.destination_images.iterdir()
    )

    forbidden_sentinels = [
        b"SENTINEL-SCAN",
        b"SENTINEL-INSTALLATION",
        b"SENTINEL-IP",
        b"SENTINEL-PHONE",
        b"SENTINEL-FREE-TEXT",
        b"SENTINEL-CAMERA",
        b"SENTINEL-UNAPPROVED",
        b"203.0.113.99",
        b"198.51.100.44",
    ]
    destination_bytes = paths.destination_database.read_bytes() + b"".join(
        image.read_bytes() for image in paths.destination_images.iterdir()
    )
    assert all(sentinel not in destination_bytes for sentinel in forbidden_sentinels)

    archive = export_training_data.export_training_data(
        paths.destination_database,
        paths.destination_images,
        tmp_path / "exports",
        now=datetime(2026, 8, 7, 13, 0, tzinfo=UTC),
    )
    with tarfile.open(archive, "r:gz") as exported:
        archive_bytes = b"".join(
            exported.extractfile(member).read()
            for member in exported.getmembers()
            if member.isfile()
        )
    assert all(sentinel not in archive_bytes for sentinel in forbidden_sentinels)


def test_offline_human_verdict_remains_authoritative_after_feedback_join(
    legacy_material,
):
    paths = legacy_material
    with closing(sqlite3.connect(paths.labeling_database)) as connection, connection:
        connection.execute("UPDATE candidates SET status = 'yes' WHERE id = 1")

    migration.apply(paths, consent_version="legacy-reviewed-v1")
    with closing(sqlite3.connect(paths.destination_database)) as connection, connection:
        status, provenance, user_correct = connection.execute(
            "SELECT review_status, label_provenance, user_correct "
            "FROM training_samples WHERE predicted_class = 'healthy'"
        ).fetchone()
    assert (status, provenance, user_correct) == (
        "offline_yes",
        "offline_agent",
        0,
    )


def test_apply_refuses_existing_destination(legacy_material):
    legacy_material.destination_database.parent.mkdir(parents=True)
    legacy_material.destination_database.write_bytes(b"do-not-overwrite")
    with pytest.raises(migration.MigrationError, match="must not already exist"):
        migration.apply(legacy_material, consent_version="legacy-reviewed-v1")
    assert legacy_material.destination_database.read_bytes() == b"do-not-overwrite"
