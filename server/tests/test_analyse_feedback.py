"""Coverage for the offline feedback analysis script.

The script is pure I/O — sqlite + filesystem — so the tests run the
real ``main()`` against a hand-built feedback DB. No mocking of
internal helpers; the report shape is the public contract.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools import analyse_feedback as af


def _make_db(path: Path) -> None:
    """Build a fresh feedback.db with the full schema the script
    expects (including the late-added ``out_of_head`` /
    ``prediction_set_json`` / ``image_path`` columns).
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE feedback (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id               TEXT    NOT NULL,
            correct               INTEGER NOT NULL,
            corrected_class       TEXT,
            note                  TEXT,
            client_ip             TEXT,
            created_at            INTEGER NOT NULL,
            predicted_class       TEXT,
            predicted_confidence  REAL,
            top3_json             TEXT,
            model_version         TEXT,
            app_version           TEXT,
            locale                TEXT,
            platform              TEXT,
            user_pseudo_id        TEXT,
            region_count          INTEGER,
            primary_region_index  INTEGER,
            feedback_latency_ms   INTEGER,
            reason                TEXT,
            image_hash            TEXT,
            client_ip_hash        TEXT,
            out_of_head           INTEGER,
            prediction_set_json   TEXT,
            image_path            TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _insert(path: Path, **row: object) -> None:
    conn = sqlite3.connect(path)
    cols = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT INTO feedback ({cols}) VALUES ({placeholders})",
        tuple(row.values()),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    db = tmp_path / "feedback.db"
    _make_db(db)
    # Three rows that exercise every analysis branch:
    #   1. Correct healthy verdict, in-head, no correction.
    #   2. Wrong healthy → leaf_spot (in-head correction, classic
    #      class-imbalance miss). Carries a top3 + prediction_set
    #      that includes the corrected class — should count toward
    #      conformal coverage.
    #   3. Wrong viral_disease → nutrient_deficiency (OUT-OF-HEAD —
    #      drives the label-set-expansion narrative). No prediction
    #      set + has an ``image_path`` (with no file on disk so the
    #      "wrong with image on disk" counter stays 0).
    common = {"created_at": 1_700_000_000, "predicted_confidence": 0.55}
    _insert(
        db,
        scan_id="scan-aaaaaaaa", correct=1,
        predicted_class="healthy",
        top3_json='[{"class":"healthy","prob":0.91}]',
        prediction_set_json='["healthy"]',
        model_version="2.1", app_version="1.0", locale="en",
        platform="android", region_count=2,
        primary_region_index=0, feedback_latency_ms=4500,
        **common,
    )
    _insert(
        db,
        scan_id="scan-bbbbbbbb", correct=0,
        corrected_class="leaf_spot", reason="wrong_class",
        predicted_class="healthy",
        top3_json='[{"class":"healthy","prob":0.55},{"class":"leaf_spot","prob":0.40}]',
        prediction_set_json='["healthy","leaf_spot"]',
        out_of_head=0,
        model_version="2.1", app_version="1.0", locale="en",
        platform="android", region_count=3,
        primary_region_index=0, feedback_latency_ms=9000,
        image_hash="a" * 64, image_path="aaaa.jpg",
        **common,
    )
    _insert(
        db,
        scan_id="scan-cccccccc", correct=0,
        corrected_class="nutrient_deficiency", reason="ambiguous_symptoms",
        predicted_class="viral_disease",
        top3_json='[{"class":"viral_disease","prob":0.45}]',
        out_of_head=1,
        model_version="2.1", app_version="1.0", locale="ru",
        platform="android", region_count=1,
        primary_region_index=0, feedback_latency_ms=12000,
        **common,
    )
    return db


def test_load_rows_handles_missing_late_columns(tmp_path: Path) -> None:
    """Older deployments may not have ``out_of_head`` /
    ``prediction_set_json`` / ``image_path``. The loader must
    tolerate that without raising — late-added columns are looked up
    via ``PRAGMA table_info`` first.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE feedback (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id               TEXT    NOT NULL,
            correct               INTEGER NOT NULL,
            created_at            INTEGER NOT NULL,
            corrected_class       TEXT, note TEXT, client_ip TEXT,
            predicted_class       TEXT, predicted_confidence REAL,
            top3_json             TEXT, model_version TEXT,
            app_version           TEXT, locale TEXT, platform TEXT,
            user_pseudo_id        TEXT, region_count INTEGER,
            primary_region_index  INTEGER, feedback_latency_ms INTEGER,
            reason                TEXT, image_hash TEXT,
            client_ip_hash        TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO feedback (scan_id, correct, created_at) "
        "VALUES (?, ?, ?)",
        ("scan-legacy01", 1, 1_700_000_000),
    )
    conn.commit()
    conn.close()
    rows = af._load_rows(db, None, since_ts=None)
    assert len(rows) == 1
    assert rows[0].out_of_head is None
    assert rows[0].prediction_set == []
    assert rows[0].image_path is None


def test_build_report_overall_and_per_class(populated_db: Path) -> None:
    rows = af._load_rows(populated_db, None, since_ts=None)
    report = af.build_report(rows)
    overall = report["overall"]
    assert overall["total"] == 3
    assert overall["correct"] == 1
    assert overall["incorrect"] == 2
    # Per-class accuracy is keyed on the (post-reroute) predicted_class
    # the client echoed back — healthy here is 1/2 correct.
    healthy = report["per_class_accuracy"]["healthy"]
    assert healthy == {
        "n": 2,
        "correct": 1,
        "accuracy": 0.5,
        "accuracy_ci95": pytest.approx(healthy["accuracy_ci95"]),
    }
    assert "viral_disease" in report["per_class_accuracy"]


def test_build_report_confusion_and_corrections(populated_db: Path) -> None:
    rows = af._load_rows(populated_db, None, since_ts=None)
    report = af.build_report(rows)
    assert report["confusion"]["healthy"]["leaf_spot"] == 1
    assert report["confusion"]["viral_disease"]["nutrient_deficiency"] == 1
    pairs = {(p["predicted"], p["corrected"]): p for p in report["top_corrections"]}
    assert ("healthy", "leaf_spot") in pairs
    assert ("viral_disease", "nutrient_deficiency") in pairs
    assert pairs[("healthy", "leaf_spot")]["count"] == 1


def test_build_report_prediction_set_coverage(populated_db: Path) -> None:
    rows = af._load_rows(populated_db, None, since_ts=None)
    report = af.build_report(rows)
    cps = report["prediction_set_coverage"]
    # One eligible wrong row (scan-b…) carries a prediction set that
    # includes its corrected class — that's the calibration-honest case.
    # The other wrong row (scan-c…) has no prediction set, so it's
    # excluded from the denominator.
    assert cps["n"] == 1
    assert cps["covered"] == 1
    assert cps["coverage"] == 1.0


def test_build_report_out_of_head_signal(populated_db: Path) -> None:
    rows = af._load_rows(populated_db, None, since_ts=None)
    ooh = af.build_report(rows)["out_of_head"]
    assert ooh["total_corrections"] == 2
    assert ooh["out_of_head_total"] == 1
    assert ooh["by_class"]["nutrient_deficiency"] == 1
    assert ooh["by_class"]["leaf_spot"] == 1


def test_build_report_calibration_buckets(populated_db: Path) -> None:
    rows = af._load_rows(populated_db, None, since_ts=None)
    cb = af.build_report(rows)["calibration_buckets"]
    # All three rows land in the (0.50, 0.70] bucket.
    mid = next(b for b in cb if b["lo"] == 0.50)
    assert mid["n"] == 3
    assert mid["correct"] == 1


def test_main_writes_full_artefact_set(populated_db: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "report"
    rc = af.main([
        "--db", str(populated_db),
        "--days", "0",
        "--out", str(out_dir),
    ])
    assert rc == 0
    for fname in ("summary.md", "metrics.json", "rows.csv",
                  "confusion.csv", "corrections.csv"):
        assert (out_dir / fname).exists(), f"missing artefact {fname}"
    metrics = json.loads((out_dir / "metrics.json").read_text())
    assert metrics["row_count"] == 3
    # The Markdown summary should mention the dominant correction pair.
    summary = (out_dir / "summary.md").read_text()
    assert "leaf_spot" in summary
    assert "nutrient_deficiency" in summary
    # rows.csv has one header line + one row per feedback.
    rows_lines = (out_dir / "rows.csv").read_text().splitlines()
    assert len(rows_lines) == 4


def test_main_prints_markdown_when_no_out_dir(
    populated_db: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = af.main(["--db", str(populated_db), "--days", "0"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "# Feedback analysis" in captured.out
    assert "Per-class accuracy" in captured.out


def test_main_errors_when_db_missing(tmp_path: Path) -> None:
    """The script should fail loudly rather than silently producing an
    empty report when the operator points it at a nonexistent DB.
    """
    with pytest.raises(SystemExit):
        af.main(["--db", str(tmp_path / "nope.db"), "--days", "0"])
