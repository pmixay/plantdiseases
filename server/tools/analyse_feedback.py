"""
Deep-analysis report over the production feedback corpus.

What it does
------------
Reads ``server/feedback.db`` (and optionally ``server/community.db``),
joins each feedback row against its persisted retraining signals
(``predicted_class`` / ``corrected_class`` / ``top3_json`` /
``prediction_set_json`` / ``image_path`` / ``out_of_head`` /
``model_version`` / ``app_version`` / ``locale`` / ``platform``), and
produces a structured + human-readable picture of where the model is
right, where it's wrong, and where it's overconfident.

What it deliberately does NOT do
--------------------------------
* Retrain anything. There is no model-training code in this repository.
* Mutate ``feedback.db``. The script opens the DB read-only.
* Talk to the network. Pure offline analysis over local SQLite + the
  on-disk feedback-image dir.

Usage
-----
::

    # Print a Markdown summary to stdout, last 30 days, default DB path.
    python -m server.tools.analyse_feedback

    # Write the full artefact set (summary.md / metrics.json / 3 CSVs)
    # under ``out/feedback-analysis/`` for a notebook to ingest.
    python -m server.tools.analyse_feedback --out out/feedback-analysis

    # Look at a different DB / a different window.
    python -m server.tools.analyse_feedback \\
        --db /var/lib/plantdiseases/feedback.db --days 90

Outputs (when ``--out`` is set)
-------------------------------
* ``summary.md``      — bullet-point report meant for a human + diff.
* ``metrics.json``    — every aggregate the script computed, structured
                        for ``pandas.read_json`` / notebook ingestion.
* ``rows.csv``        — flat row-level export (one feedback row per
                        line, with ``top3`` / ``prediction_set`` /
                        ``image_present`` resolved). Drop straight into
                        a notebook ``pd.read_csv`` cell.
* ``confusion.csv``   — predicted × corrected confusion counts.
* ``corrections.csv`` — top (predicted → corrected) pairs sorted by
                        frequency, with the count and the share of the
                        predicted bucket they represent.

Exit status
-----------
0 on success (even when the DB is empty), 2 when the DB is missing
or the schema looks wrong.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Configuration knobs ─────────────────────────────────────────────

# Production head — kept in sync with ``_FEEDBACK_IN_HEAD_CLASSES`` in
# ``server/main.py``. Hard-coded rather than imported so the script
# stands alone (a notebook can run it against a DB snapshot without
# wiring up the FastAPI app's import graph).
IN_HEAD_CLASSES: frozenset[str] = frozenset({
    "bacterial_leaf_spot",
    "gray_mold_botrytis",
    "healthy",
    "leaf_spot_fungal",
    "not_a_plant",
    "powdery_mildew",
    "viral_disease",
})

# Confidence-bucket edges for the calibration table. Open lower bound
# on each bucket, closed upper bound — i.e. (0.0, 0.5] / (0.5, 0.7] /
# (0.7, 0.9] / (0.9, 1.0]. Buckets cap accuracy estimates against the
# usual "confidence vs correctness" honesty plot.
CONFIDENCE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.50),
    (0.50, 0.70),
    (0.70, 0.90),
    (0.90, 1.0001),
)

# Minimum sample count before a per-class accuracy figure is shown
# without a "n=X, take with salt" caveat in the summary. Numbers
# below this still land in the JSON / CSV outputs — the threshold
# only governs the Markdown narrative.
MIN_SAMPLES_FOR_CONFIDENT_ACCURACY = 20

logger = logging.getLogger("analyse_feedback")


# ── Row model ───────────────────────────────────────────────────────


@dataclass
class FeedbackRow:
    """One feedback row, normalised for analysis.

    Columns mirror the schema in ``server/feedback.py``; JSON-shaped
    columns (``top3_json``, ``prediction_set_json``) are parsed eagerly
    so the analysis loop doesn't have to. ``image_present`` is computed
    by stat-ing the path under ``FEEDBACK_IMG_DIR`` (when configured).
    """

    id: int
    scan_id: str
    correct: bool
    created_at: int
    corrected_class: str | None
    reason: str | None
    note: str | None
    predicted_class: str | None
    predicted_confidence: float | None
    top3: list[tuple[str, float]] = field(default_factory=list)
    prediction_set: list[str] = field(default_factory=list)
    out_of_head: bool | None = None
    model_version: str | None = None
    app_version: str | None = None
    locale: str | None = None
    platform: str | None = None
    user_pseudo_id: str | None = None
    region_count: int | None = None
    primary_region_index: int | None = None
    feedback_latency_ms: int | None = None
    image_hash: str | None = None
    image_path: str | None = None
    image_present: bool = False


def _parse_top3(raw: str | None) -> list[tuple[str, float]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[tuple[str, float]] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        cls = entry.get("class")
        prob = entry.get("prob")
        if not isinstance(cls, str) or not isinstance(prob, (int, float)):
            continue
        out.append((cls, float(prob)))
    return out


def _parse_class_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(c) for c in data if isinstance(c, str)]


def _row_to_dataclass(
    row: sqlite3.Row,
    columns: frozenset[str],
    image_dir: Path | None,
) -> FeedbackRow:
    """Adapt a sqlite ``Row`` into a typed ``FeedbackRow``.

    ``columns`` is the set of column names actually present on this
    DB (the late-added ones — ``image_path`` / ``out_of_head`` /
    ``prediction_set_json`` — may or may not exist). Computed once
    per ``_load_rows`` pass and passed in so we don't probe the
    schema for every row.
    """
    image_path = row["image_path"] if "image_path" in columns else None
    image_present = False
    if image_path and image_dir is not None:
        image_present = (image_dir / image_path).is_file()
    out_of_head_raw = row["out_of_head"] if "out_of_head" in columns else None
    prediction_set_raw = (
        row["prediction_set_json"]
        if "prediction_set_json" in columns
        else None
    )
    return FeedbackRow(
        id=int(row["id"]),
        scan_id=str(row["scan_id"]),
        correct=bool(row["correct"]),
        created_at=int(row["created_at"]),
        corrected_class=row["corrected_class"],
        reason=row["reason"],
        note=row["note"],
        predicted_class=row["predicted_class"],
        predicted_confidence=(
            float(row["predicted_confidence"])
            if row["predicted_confidence"] is not None
            else None
        ),
        top3=_parse_top3(row["top3_json"]),
        prediction_set=_parse_class_list(prediction_set_raw),
        out_of_head=(
            None if out_of_head_raw is None else bool(out_of_head_raw)
        ),
        model_version=row["model_version"],
        app_version=row["app_version"],
        locale=row["locale"],
        platform=row["platform"],
        user_pseudo_id=row["user_pseudo_id"],
        region_count=(
            int(row["region_count"]) if row["region_count"] is not None else None
        ),
        primary_region_index=(
            int(row["primary_region_index"])
            if row["primary_region_index"] is not None
            else None
        ),
        feedback_latency_ms=(
            int(row["feedback_latency_ms"])
            if row["feedback_latency_ms"] is not None
            else None
        ),
        image_hash=row["image_hash"],
        image_path=image_path,
        image_present=image_present,
    )


# ── DB access ───────────────────────────────────────────────────────


def _load_rows(
    db_path: Path,
    image_dir: Path | None,
    since_ts: int | None,
) -> list[FeedbackRow]:
    """Pull feedback rows from ``db_path`` newer than ``since_ts`` (epoch
    seconds). Read-only connection — we never write back even by accident.
    """
    if not db_path.exists():
        raise SystemExit(f"feedback DB not found: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # PRAGMA table_info first so we tolerate older schemas that
        # don't yet carry the late-added columns (``out_of_head``,
        # ``prediction_set_json``, ``image_path``).
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(feedback)").fetchall()
        }
        required = {"id", "scan_id", "correct", "created_at"}
        if not required.issubset(cols):
            raise SystemExit(
                "feedback DB schema does not look right "
                f"(missing columns: {sorted(required - cols)})"
            )
        select_cols = [
            "id", "scan_id", "correct", "created_at",
            "corrected_class", "reason", "note", "predicted_class",
            "predicted_confidence", "top3_json", "model_version",
            "app_version", "locale", "platform", "user_pseudo_id",
            "region_count", "primary_region_index",
            "feedback_latency_ms", "image_hash",
        ]
        if "out_of_head" in cols:
            select_cols.append("out_of_head")
        if "prediction_set_json" in cols:
            select_cols.append("prediction_set_json")
        if "image_path" in cols:
            select_cols.append("image_path")
        sql = f"SELECT {', '.join(select_cols)} FROM feedback"
        params: tuple = ()
        if since_ts is not None:
            sql += " WHERE created_at >= ?"
            params = (since_ts,)
        sql += " ORDER BY created_at ASC"
        # Snapshot the present-column set once and reuse it for every
        # row — ``PRAGMA table_info`` is cheap but unnecessary per-row,
        # and ``sqlite3.Row`` has no ``__contains__`` so a per-row
        # ``"x" in row.keys()`` would be the only alternative.
        present_cols = frozenset(select_cols)
        return [
            _row_to_dataclass(r, present_cols, image_dir)
            for r in conn.execute(sql, params)
        ]
    finally:
        conn.close()


# ── Aggregations ────────────────────────────────────────────────────


def _split(value: float | int, total: int) -> float:
    return float(value) / total if total else 0.0


def _wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Two-sided Wilson 95 % CI for a binomial proportion.

    Small-sample-honest alternative to the normal approximation —
    important here because a lot of per-class buckets have < 30 rows.
    """
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    ) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _overall(rows: list[FeedbackRow]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(1 for r in rows if r.correct)
    incorrect = total - correct
    lo, hi = _wilson_ci(correct, total)
    latencies = [r.feedback_latency_ms for r in rows if r.feedback_latency_ms is not None]
    return {
        "total": total,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy": _split(correct, total),
        "accuracy_ci95": [lo, hi],
        "with_correction": sum(
            1 for r in rows if r.corrected_class and not r.correct
        ),
        "with_image_on_disk": sum(1 for r in rows if r.image_present),
        "with_prediction_set": sum(1 for r in rows if r.prediction_set),
        "with_top3": sum(1 for r in rows if r.top3),
        "latency_ms": {
            "n": len(latencies),
            "median": int(statistics.median(latencies)) if latencies else None,
            "p90": (
                int(statistics.quantiles(latencies, n=10)[8])
                if len(latencies) >= 10
                else None
            ),
            "max": max(latencies) if latencies else None,
        },
    }


def _per_class_accuracy(rows: list[FeedbackRow]) -> dict[str, dict[str, Any]]:
    """Accuracy + CI per predicted class. Predicted class is the row's
    ``predicted_class`` column, which the client now derives from the
    POST-reroute server verdict (the displayed one), so figures here
    reflect what users actually saw."""
    by_class: dict[str, list[FeedbackRow]] = defaultdict(list)
    for r in rows:
        if r.predicted_class:
            by_class[r.predicted_class].append(r)
    out: dict[str, dict[str, Any]] = {}
    for cls, bucket in by_class.items():
        correct = sum(1 for r in bucket if r.correct)
        lo, hi = _wilson_ci(correct, len(bucket))
        out[cls] = {
            "n": len(bucket),
            "correct": correct,
            "accuracy": _split(correct, len(bucket)),
            "accuracy_ci95": [lo, hi],
        }
    return out


def _confusion(rows: list[FeedbackRow]) -> dict[str, dict[str, int]]:
    """``predicted_class → corrected_class → count`` for ``correct=0``
    rows that carry an explicit correction. Healthy → disease pairs
    are the high-signal rows for the post-launch class-imbalance bug.
    """
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r.correct or not r.corrected_class or not r.predicted_class:
            continue
        out[r.predicted_class][r.corrected_class] += 1
    return {k: dict(v) for k, v in out.items()}


def _top_corrections(
    confusion: dict[str, dict[str, int]],
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Flattened, ranked view of the confusion matrix. Same rows as
    ``confusion``, just easier for a notebook to ``read_csv`` and plot.
    """
    pred_totals = {p: sum(d.values()) for p, d in confusion.items()}
    pairs: list[dict[str, Any]] = []
    for predicted, mapping in confusion.items():
        total = pred_totals[predicted] or 1
        for corrected, count in mapping.items():
            pairs.append({
                "predicted": predicted,
                "corrected": corrected,
                "count": count,
                "share_of_predicted_corrections": count / total,
            })
    pairs.sort(key=lambda p: p["count"], reverse=True)
    return pairs[:top_n]


def _calibration_buckets(rows: list[FeedbackRow]) -> list[dict[str, Any]]:
    """Accuracy stratified by predicted confidence. A well-calibrated
    model has ``accuracy ≈ midpoint(bucket)`` for each bucket. Wide
    gaps point at over- (or under-) confidence the post-processing
    gates aren't compensating for.
    """
    out: list[dict[str, Any]] = []
    for lo, hi in CONFIDENCE_BUCKETS:
        bucket = [
            r for r in rows
            if r.predicted_confidence is not None
            and lo < r.predicted_confidence <= hi
        ]
        n = len(bucket)
        correct = sum(1 for r in bucket if r.correct)
        ci_lo, ci_hi = _wilson_ci(correct, n)
        out.append({
            "lo": lo,
            "hi": hi,
            "n": n,
            "correct": correct,
            "accuracy": _split(correct, n),
            "accuracy_ci95": [ci_lo, ci_hi],
            "mean_confidence": (
                statistics.fmean(r.predicted_confidence for r in bucket)  # type: ignore[arg-type]
                if bucket else None
            ),
        })
    return out


def _prediction_set_coverage(rows: list[FeedbackRow]) -> dict[str, Any]:
    """When the user supplied a correction AND the original row carried
    a conformal prediction set, was the correction inside the set?
    This is the canonical "calibration drift vs point-estimate failure"
    split: high coverage with low top-1 accuracy means the set is
    honest but the argmax is wrong; low coverage means the model's
    uncertainty estimate itself is broken.
    """
    eligible = [
        r for r in rows
        if not r.correct and r.corrected_class and r.prediction_set
    ]
    if not eligible:
        return {"n": 0, "covered": 0, "coverage": None, "mean_set_size": None}
    covered = sum(1 for r in eligible if r.corrected_class in r.prediction_set)
    lo, hi = _wilson_ci(covered, len(eligible))
    return {
        "n": len(eligible),
        "covered": covered,
        "coverage": covered / len(eligible),
        "coverage_ci95": [lo, hi],
        "mean_set_size": statistics.fmean(len(r.prediction_set) for r in eligible),
    }


def _out_of_head(rows: list[FeedbackRow]) -> dict[str, Any]:
    """Distribution of out-of-head corrections — what users are asking
    us to predict that the current head can't. Drives "should we grow
    the head with this class next?" decisions in a notebook, but does
    not trigger anything automated.
    """
    corrections_by_class: Counter[str] = Counter()
    out_of_head_count = 0
    for r in rows:
        if r.correct or not r.corrected_class:
            continue
        corrections_by_class[r.corrected_class] += 1
        if r.corrected_class not in IN_HEAD_CLASSES:
            out_of_head_count += 1
    total_corrections = sum(corrections_by_class.values())
    return {
        "total_corrections": total_corrections,
        "out_of_head_total": out_of_head_count,
        "out_of_head_share": _split(out_of_head_count, total_corrections),
        "by_class": dict(corrections_by_class.most_common()),
    }


def _by_dimension(
    rows: list[FeedbackRow],
    key: str,
) -> dict[str, dict[str, Any]]:
    """Accuracy grouped by an arbitrary string-valued column. Used for
    the per-version, per-locale, per-platform breakdowns.
    """
    buckets: dict[str, list[FeedbackRow]] = defaultdict(list)
    for r in rows:
        v = getattr(r, key)
        buckets[str(v) if v is not None else "<unset>"].append(r)
    out: dict[str, dict[str, Any]] = {}
    for k, b in buckets.items():
        correct = sum(1 for r in b if r.correct)
        lo, hi = _wilson_ci(correct, len(b))
        out[k] = {
            "n": len(b),
            "correct": correct,
            "accuracy": _split(correct, len(b)),
            "accuracy_ci95": [lo, hi],
        }
    return out


def _reasons(rows: list[FeedbackRow]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in rows:
        if r.reason:
            c[r.reason] += 1
    return dict(c.most_common())


def _detector_signal(rows: list[FeedbackRow]) -> dict[str, Any]:
    """How does feedback correctness correlate with the detector's
    region count + which region the classifier ended up on? A
    primary_region_index that consistently == 0 on wrong rows is the
    "we picked the first / biggest leaf, not the diseased one" tell.
    """
    region_counts = [r.region_count for r in rows if r.region_count is not None]
    primary_idx_on_wrong = [
        r.primary_region_index for r in rows
        if not r.correct and r.primary_region_index is not None
    ]
    return {
        "region_count_distribution": dict(Counter(region_counts).most_common()),
        "primary_region_index_on_wrong": dict(
            Counter(primary_idx_on_wrong).most_common()
        ),
        "wrong_with_region_index_zero": sum(
            1 for v in primary_idx_on_wrong if v == 0
        ),
    }


def _time_series(rows: list[FeedbackRow]) -> list[dict[str, Any]]:
    """Daily counts + accuracy. Drives a notebook trendline.
    """
    by_day: dict[str, list[FeedbackRow]] = defaultdict(list)
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(r.created_at))
        by_day[day].append(r)
    out: list[dict[str, Any]] = []
    for day in sorted(by_day):
        bucket = by_day[day]
        correct = sum(1 for r in bucket if r.correct)
        out.append({
            "day": day,
            "n": len(bucket),
            "correct": correct,
            "accuracy": _split(correct, len(bucket)),
        })
    return out


def _image_coverage(rows: list[FeedbackRow]) -> dict[str, Any]:
    """How often we have the actual bytes for a wrong-call row.
    Without the bytes, retraining can only learn from the label —
    not from the image.
    """
    wrong_rows = [r for r in rows if not r.correct]
    return {
        "wrong_total": len(wrong_rows),
        "wrong_with_image_path": sum(1 for r in wrong_rows if r.image_path),
        "wrong_with_image_on_disk": sum(1 for r in wrong_rows if r.image_present),
    }


# ── Assembly ────────────────────────────────────────────────────────


def build_report(rows: list[FeedbackRow]) -> dict[str, Any]:
    confusion = _confusion(rows)
    return {
        "generated_at": int(time.time()),
        "row_count": len(rows),
        "overall": _overall(rows),
        "per_class_accuracy": _per_class_accuracy(rows),
        "confusion": confusion,
        "top_corrections": _top_corrections(confusion),
        "calibration_buckets": _calibration_buckets(rows),
        "prediction_set_coverage": _prediction_set_coverage(rows),
        "out_of_head": _out_of_head(rows),
        "by_model_version": _by_dimension(rows, "model_version"),
        "by_app_version": _by_dimension(rows, "app_version"),
        "by_locale": _by_dimension(rows, "locale"),
        "by_platform": _by_dimension(rows, "platform"),
        "reasons": _reasons(rows),
        "detector_signal": _detector_signal(rows),
        "daily": _time_series(rows),
        "image_coverage": _image_coverage(rows),
    }


# ── Renderers ───────────────────────────────────────────────────────


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:5.1f}%"


def _fmt_ci(ci: list[float] | None) -> str:
    if not ci:
        return ""
    return f" (95% CI {ci[0] * 100:.1f}–{ci[1] * 100:.1f}%)"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    overall = report["overall"]
    lines.append(f"# Feedback analysis — {report['row_count']} rows")
    lines.append("")
    lines.append(
        f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(report['generated_at']))}."
    )
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append(f"- Total rows: **{overall['total']}**")
    lines.append(
        f"- Correct: **{overall['correct']}** "
        f"({_fmt_pct(overall['accuracy'])}{_fmt_ci(overall['accuracy_ci95'])})"
    )
    lines.append(f"- Incorrect: {overall['incorrect']}")
    lines.append(
        f"- With explicit correction: {overall['with_correction']}"
    )
    lines.append(
        f"- With image bytes on disk: {overall['with_image_on_disk']}"
    )
    lines.append(
        f"- With prediction-set persisted: {overall['with_prediction_set']}"
    )
    lat = overall["latency_ms"]
    if lat["median"] is not None:
        lines.append(
            f"- Feedback latency: median {lat['median']} ms, p90 "
            f"{lat['p90'] or '—'} ms, max {lat['max']} ms (n={lat['n']})"
        )
    lines.append("")

    lines.append("## Per-class accuracy")
    lines.append("")
    lines.append("| Class | n | accuracy | 95% CI |")
    lines.append("|---|---:|---:|---|")
    per_class = sorted(
        report["per_class_accuracy"].items(),
        key=lambda kv: kv[1]["n"], reverse=True,
    )
    for cls, stats in per_class:
        caveat = " ⚠ low-n" if stats["n"] < MIN_SAMPLES_FOR_CONFIDENT_ACCURACY else ""
        lines.append(
            f"| `{cls}`{caveat} | {stats['n']} | "
            f"{_fmt_pct(stats['accuracy'])} | "
            f"{stats['accuracy_ci95'][0] * 100:.1f}–{stats['accuracy_ci95'][1] * 100:.1f}% |"
        )
    lines.append("")

    lines.append("## Top correction pairs (predicted → corrected)")
    lines.append("")
    if not report["top_corrections"]:
        lines.append("_no corrections in window_")
    else:
        lines.append("| predicted | corrected | count | share of predicted's corrections |")
        lines.append("|---|---|---:|---:|")
        for pair in report["top_corrections"]:
            lines.append(
                f"| `{pair['predicted']}` | `{pair['corrected']}` | "
                f"{pair['count']} | "
                f"{_fmt_pct(pair['share_of_predicted_corrections'])} |"
            )
    lines.append("")

    lines.append("## Calibration (accuracy vs predicted confidence)")
    lines.append("")
    lines.append("| confidence bucket | n | mean conf | accuracy | gap |")
    lines.append("|---|---:|---:|---:|---:|")
    for b in report["calibration_buckets"]:
        if b["n"] == 0:
            lines.append(f"| {b['lo']:.2f}–{b['hi']:.2f} | 0 | — | — | — |")
            continue
        gap = b["accuracy"] - (b["mean_confidence"] or 0.0)
        lines.append(
            f"| {b['lo']:.2f}–{b['hi']:.2f} | {b['n']} | "
            f"{_fmt_pct(b['mean_confidence'])} | "
            f"{_fmt_pct(b['accuracy'])} | {gap * 100:+.1f}pp |"
        )
    lines.append("")
    lines.append(
        "_Negative gap = over-confidence (model claimed higher probability "
        "than the empirical accuracy); positive = under-confidence._"
    )
    lines.append("")

    cps = report["prediction_set_coverage"]
    lines.append("## Conformal-set coverage on wrong rows")
    lines.append("")
    if cps["n"] == 0:
        lines.append("_no eligible rows (need wrong rows with both a correction and a persisted prediction set)_")
    else:
        lines.append(
            f"- Eligible wrong rows: {cps['n']}"
        )
        lines.append(
            f"- Correct answer inside the set: {cps['covered']} "
            f"({_fmt_pct(cps['coverage'])}"
            f"{_fmt_ci(cps.get('coverage_ci95'))})"
        )
        lines.append(
            f"- Mean prediction-set size: {cps['mean_set_size']:.2f}"
        )
        lines.append("")
        lines.append(
            "_High coverage + low top-1 accuracy → calibration is honest, "
            "the argmax just keeps losing → point-estimate problem (re-rank). "
            "Low coverage → the model's uncertainty itself is broken → "
            "recalibrate / pick a different OOD score._"
        )
    lines.append("")

    ooh = report["out_of_head"]
    lines.append("## Out-of-head corrections (label-set expansion signal)")
    lines.append("")
    lines.append(f"- Total corrections: {ooh['total_corrections']}")
    lines.append(
        f"- Out-of-head: {ooh['out_of_head_total']} "
        f"({_fmt_pct(ooh['out_of_head_share'])})"
    )
    if ooh["by_class"]:
        lines.append("")
        lines.append("| corrected_class | in-head? | count |")
        lines.append("|---|---|---:|")
        for cls, count in ooh["by_class"].items():
            in_head = "✓" if cls in IN_HEAD_CLASSES else " "
            lines.append(f"| `{cls}` | {in_head} | {count} |")
    lines.append("")

    for label, key in (
        ("model version", "by_model_version"),
        ("app version", "by_app_version"),
        ("locale", "by_locale"),
        ("platform", "by_platform"),
    ):
        block = report[key]
        if not block:
            continue
        lines.append(f"## Accuracy by {label}")
        lines.append("")
        lines.append(f"| {label} | n | accuracy | 95% CI |")
        lines.append("|---|---:|---:|---|")
        for k, stats in sorted(block.items(), key=lambda kv: kv[1]["n"], reverse=True):
            lines.append(
                f"| `{k}` | {stats['n']} | "
                f"{_fmt_pct(stats['accuracy'])} | "
                f"{stats['accuracy_ci95'][0] * 100:.1f}–{stats['accuracy_ci95'][1] * 100:.1f}% |"
            )
        lines.append("")

    if report["reasons"]:
        lines.append("## User-supplied reasons (wrong-call sheet)")
        lines.append("")
        lines.append("| reason | count |")
        lines.append("|---|---:|")
        for reason, count in report["reasons"].items():
            lines.append(f"| `{reason}` | {count} |")
        lines.append("")

    det = report["detector_signal"]
    lines.append("## Detector signal on wrong rows")
    lines.append("")
    lines.append(
        f"- Region-count distribution: "
        f"{det['region_count_distribution']}"
    )
    lines.append(
        f"- Primary-region-index on wrong rows: "
        f"{det['primary_region_index_on_wrong']}"
    )
    lines.append(
        f"- Wrong rows with `primary_region_index == 0`: "
        f"{det['wrong_with_region_index_zero']}"
    )
    lines.append("")

    img = report["image_coverage"]
    lines.append("## Image-bytes coverage on wrong rows")
    lines.append("")
    lines.append(
        f"- Wrong rows total: {img['wrong_total']}"
    )
    lines.append(
        f"- Wrong rows with ``image_path`` set: {img['wrong_with_image_path']}"
    )
    lines.append(
        f"- Wrong rows whose image bytes are actually on disk: "
        f"{img['wrong_with_image_on_disk']}"
    )
    lines.append("")

    daily = report["daily"]
    if daily:
        lines.append("## Daily trend (last days in window)")
        lines.append("")
        lines.append("| day | n | accuracy |")
        lines.append("|---|---:|---:|")
        for d in daily[-30:]:
            lines.append(
                f"| {d['day']} | {d['n']} | {_fmt_pct(d['accuracy'])} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── CSV exporters ───────────────────────────────────────────────────


def write_rows_csv(rows: list[FeedbackRow], out: Path) -> None:
    header = [
        "id", "scan_id", "created_at", "correct", "predicted_class",
        "predicted_confidence", "corrected_class", "reason",
        "out_of_head", "in_prediction_set", "prediction_set_size",
        "model_version", "app_version", "locale", "platform",
        "user_pseudo_id", "region_count", "primary_region_index",
        "feedback_latency_ms", "image_hash", "image_path",
        "image_present", "top3", "note_len",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            in_set = (
                r.corrected_class in r.prediction_set
                if r.prediction_set and r.corrected_class
                else None
            )
            w.writerow([
                r.id, r.scan_id, r.created_at, int(r.correct),
                r.predicted_class or "", r.predicted_confidence or "",
                r.corrected_class or "", r.reason or "",
                "" if r.out_of_head is None else int(r.out_of_head),
                "" if in_set is None else int(in_set),
                len(r.prediction_set),
                r.model_version or "", r.app_version or "",
                r.locale or "", r.platform or "",
                r.user_pseudo_id or "",
                "" if r.region_count is None else r.region_count,
                "" if r.primary_region_index is None else r.primary_region_index,
                "" if r.feedback_latency_ms is None else r.feedback_latency_ms,
                r.image_hash or "", r.image_path or "",
                int(r.image_present),
                ";".join(f"{c}:{p:.4f}" for c, p in r.top3),
                len(r.note or ""),
            ])


def write_confusion_csv(confusion: dict[str, dict[str, int]], out: Path) -> None:
    keys = sorted({
        c
        for pred in confusion.values()
        for c in pred
    } | set(confusion))
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["predicted"] + keys)
        for pred in keys:
            row = [pred]
            mapping = confusion.get(pred, {})
            for corr in keys:
                row.append(str(mapping.get(corr, 0)))
            w.writerow(row)


def write_corrections_csv(
    top_corrections: list[dict[str, Any]], out: Path,
) -> None:
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["predicted", "corrected", "count", "share_of_predicted_corrections"])
        for p in top_corrections:
            w.writerow([
                p["predicted"], p["corrected"], p["count"],
                f"{p['share_of_predicted_corrections']:.4f}",
            ])


# ── Entry point ─────────────────────────────────────────────────────


def _default_db_path() -> Path:
    env = os.environ.get("FEEDBACK_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "feedback.db"


def _default_image_dir() -> Path:
    env = os.environ.get("FEEDBACK_IMG_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "feedback_images"


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deep-analysis report over the feedback corpus.",
    )
    p.add_argument(
        "--db", type=Path, default=_default_db_path(),
        help="Path to feedback.db (defaults to $FEEDBACK_DB_PATH or "
             "the repo's server/feedback.db).",
    )
    p.add_argument(
        "--image-dir", type=Path, default=_default_image_dir(),
        help="Path to the on-disk feedback-image directory (defaults "
             "to $FEEDBACK_IMG_DIR or server/feedback_images/).",
    )
    p.add_argument(
        "--days", type=int, default=30,
        help="Restrict to feedback rows from the last N days. Pass 0 "
             "to load every row in the DB.",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Output directory. When set the script writes summary.md, "
             "metrics.json, rows.csv, confusion.csv, corrections.csv "
             "into it. When unset, the Markdown summary is printed to "
             "stdout.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Log row counts to stderr while reading the DB.",
    )
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    since_ts: int | None = None
    if args.days > 0:
        since_ts = int(time.time()) - args.days * 86_400
    image_dir = args.image_dir if args.image_dir.exists() else None
    rows = _load_rows(args.db, image_dir, since_ts)
    logger.info("Loaded %d feedback rows from %s", len(rows), args.db)
    report = build_report(rows)
    markdown = render_markdown(report)
    if args.out is None:
        sys.stdout.write(markdown)
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text(markdown, encoding="utf-8")
    (args.out / "metrics.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8",
    )
    write_rows_csv(rows, args.out / "rows.csv")
    write_confusion_csv(report["confusion"], args.out / "confusion.csv")
    write_corrections_csv(
        report["top_corrections"], args.out / "corrections.csv",
    )
    logger.info("Wrote 5 artefacts under %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
