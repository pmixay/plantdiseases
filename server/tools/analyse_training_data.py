#!/usr/bin/env python3
"""Read-only inventory, calibration, and overconfidence analysis.

This tool consumes only ``training_samples``. It never reads receipt rows,
images, accounts, community data, telemetry, or logs, and it never updates the
serving database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path


class AnalysisError(RuntimeError):
    pass


def _connect(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise AnalysisError("training database does not exist")
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, isolation_level=None
    )
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    return connection


def _json_list(value: str) -> list:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _counts(rows: list[dict], field: str) -> dict[str, int]:
    return dict(
        sorted(Counter(str(row.get(field) or "unknown") for row in rows).items())
    )


def analyse(database: Path) -> dict:
    with closing(_connect(Path(database))) as connection:
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT sample_id, source, model_family, model_version, "
                    "predicted_class, predicted_confidence, top_k_json, "
                    "conformal_set_json, user_correct, corrected_class, out_of_head, "
                    "review_status, label_provenance FROM training_samples"
                )
            ]
        except sqlite3.DatabaseError as exc:
            raise AnalysisError("training database schema is invalid") from exc

    calibration: dict[int, list[tuple[float, int]]] = defaultdict(list)
    per_class: dict[str, list[int]] = defaultdict(list)
    top_k_total = top_k_hits = 0
    conformal_total = conformal_hits = 0
    overconfident: Counter[tuple[str, str]] = Counter()
    out_of_head: Counter[str] = Counter()
    labelled = 0

    for row in rows:
        correctness = row["user_correct"]
        confidence = row["predicted_confidence"]
        predicted = str(row["predicted_class"] or "unknown")
        corrected = str(row["corrected_class"] or "unknown")
        if correctness is None:
            continue
        labelled += 1
        correct = int(bool(correctness))
        per_class[predicted].append(correct)
        if confidence is not None:
            score = max(0.0, min(1.0, float(confidence)))
            calibration[min(9, int(score * 10))].append((score, correct))
            if not correct and score >= 0.8:
                overconfident[(predicted, corrected)] += 1

        expected = predicted if correct else corrected
        top_k = _json_list(row["top_k_json"])
        if top_k and expected != "unknown":
            top_k_total += 1
            classes = {
                str(item.get("class")) for item in top_k if isinstance(item, dict)
            }
            top_k_hits += int(expected in classes)
        conformal = {str(item) for item in _json_list(row["conformal_set_json"])}
        if conformal and expected != "unknown":
            conformal_total += 1
            conformal_hits += int(expected in conformal)
        if row["out_of_head"]:
            out_of_head[corrected] += 1

    calibration_rows = []
    expected_calibration_error = 0.0
    calibration_n = sum(len(values) for values in calibration.values())
    for bucket in range(10):
        values = calibration.get(bucket, [])
        if not values:
            continue
        average_confidence = sum(value[0] for value in values) / len(values)
        accuracy = sum(value[1] for value in values) / len(values)
        gap = abs(average_confidence - accuracy)
        expected_calibration_error += gap * len(values) / max(calibration_n, 1)
        calibration_rows.append(
            {
                "lower": bucket / 10,
                "upper": (bucket + 1) / 10,
                "count": len(values),
                "average_confidence": round(average_confidence, 6),
                "accuracy": round(accuracy, 6),
                "absolute_gap": round(gap, 6),
            }
        )

    return {
        "inventory": {
            "total": len(rows),
            "by_class": _counts(rows, "predicted_class"),
            "by_source": _counts(rows, "source"),
            "by_model_version": _counts(rows, "model_version"),
            "by_review_status": _counts(rows, "review_status"),
            "by_provenance": _counts(rows, "label_provenance"),
        },
        "feedback_quality": {
            "labelled_samples": labelled,
            "per_predicted_class": {
                class_name: {
                    "count": len(values),
                    "accuracy": round(sum(values) / len(values), 6),
                }
                for class_name, values in sorted(per_class.items())
            },
            "top_k": {
                "count": top_k_total,
                "coverage": (
                    round(top_k_hits / top_k_total, 6) if top_k_total else None
                ),
            },
            "conformal": {
                "count": conformal_total,
                "empirical_coverage": (
                    round(conformal_hits / conformal_total, 6)
                    if conformal_total
                    else None
                ),
            },
            "out_of_head": dict(sorted(out_of_head.items())),
        },
        "calibration": {
            "bins": calibration_rows,
            "expected_calibration_error": round(expected_calibration_error, 6),
        },
        "overconfident_errors": [
            {
                "predicted_class": predicted,
                "corrected_class": corrected,
                "count": count,
            }
            for (predicted, corrected), count in sorted(
                overconfident.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="training.db path")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON output path (refuses to overwrite an existing file)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = analyse(arguments.db)
    except AnalysisError as exc:
        raise SystemExit(f"analysis refused: {exc}") from exc
    body = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        if arguments.output.exists():
            raise SystemExit("analysis refused: output already exists")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(body, encoding="utf-8")
        arguments.output.chmod(0o444)
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
