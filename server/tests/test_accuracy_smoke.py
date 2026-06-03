"""Houseplant-domain accuracy smoke test.

Loads a held-out set of ~500 real houseplant photos collected by
``server/tools/collect_smoke_set.py`` (gitignored; lives at
``data_v2/smoke/<class>/*.jpg|png``) and asserts that the deployed
two-stage pipeline still meets the smoke ship-gate floor of
**macro-F1 ≥ 0.65** on the smoke distribution. This is the honest
field metric — distinct from ``val_realworld`` which leans on
agricultural PlantDoc images.

The test is **skipped** when either trained model artefacts or the
smoke set are absent, so CI on a fresh clone stays green. When the
test does run it also writes a per-class breakdown to
``server/models/smoke_metrics.json`` so the bacterial ↔ fungal
confusion pair can be monitored across releases — see
``MODEL_CARD.md`` "Known caveats".
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest
from PIL import Image

SERVER_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = SERVER_DIR / "models"
SMOKE_DIR = SERVER_DIR.parent / "data_v2" / "smoke"
METRICS_OUT = MODELS_DIR / "smoke_metrics.json"

# Smoke-set CI floor — 0.65 macro-F1 (distinct from the notebook §6
# val_realworld ship gate, which leans on agricultural PlantDoc images).
MACRO_F1_FLOOR = 0.65
# Minimum class coverage. The collector ships 312 / 500 in the cloud
# sandbox today (Cloudflare WAF blocks `viral_disease`); accept any
# class with ≥ 5 photos so partial runs still report metrics for the
# classes we have.
MIN_IMAGES_PER_CLASS = 5

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _have_real_pipeline() -> bool:
    """The fail-fast pipeline needs at least one classifier weight on disk."""
    cls_candidates = [
        MODELS_DIR / "classifier_int8.onnx",
        MODELS_DIR / "classifier.onnx",
        MODELS_DIR / "classifier.pth",
    ]
    det_candidates = [
        MODELS_DIR / "detector.onnx",
        MODELS_DIR / "detector.pt",
    ]
    return any(p.exists() for p in cls_candidates) and any(
        p.exists() for p in det_candidates
    )


def _collect_smoke_images() -> dict[str, list[Path]]:
    """Walk ``data_v2/smoke/<class>/`` and return `{class_name: [path, ...]}`."""
    if not SMOKE_DIR.exists():
        return {}
    out: dict[str, list[Path]] = {}
    for class_dir in sorted(p for p in SMOKE_DIR.iterdir() if p.is_dir()):
        imgs = [
            p
            for p in sorted(class_dir.iterdir())
            if p.suffix.lower() in IMG_EXTS and p.is_file()
        ]
        if imgs:
            out[class_dir.name] = imgs
    return out


def _macro_f1(per_class: dict[str, dict[str, float]]) -> float:
    f1s = [v["f1"] for v in per_class.values() if v["support"] > 0]
    return sum(f1s) / len(f1s) if f1s else 0.0


def test_accuracy_smoke():
    if not _have_real_pipeline():
        pytest.skip("trained model artefacts absent — smoke set requires real weights")
    images_by_class = _collect_smoke_images()
    if not images_by_class:
        pytest.skip("no smoke set at data_v2/smoke/ — run tools/collect_smoke_set.py")

    # Lazy-import the real pipeline only when models are present so the
    # rest of the suite never pays the ONNX session load cost.
    from pipeline import PlantDiseasePipeline

    # Prefer ONNX over .pth; falls through to whatever exists.
    classifier_path = next(
        
            p
            for p in (
                MODELS_DIR / "classifier_int8.onnx",
                MODELS_DIR / "classifier.onnx",
                MODELS_DIR / "classifier.pth",
            )
            if p.exists()
        
    )
    detector_path = next(
        p for p in (MODELS_DIR / "detector.onnx", MODELS_DIR / "detector.pt") if p.exists()
    )
    pipeline = PlantDiseasePipeline(detector_path, classifier_path)
    known_classes = set(pipeline.classifier.CLASS_NAMES)

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    support: dict[str, int] = defaultdict(int)

    skipped_unknown_classes = []
    for true_class, paths in images_by_class.items():
        if true_class not in known_classes:
            skipped_unknown_classes.append(true_class)
            continue
        if len(paths) < MIN_IMAGES_PER_CLASS:
            continue
        for p in paths:
            try:
                with Image.open(p) as img:
                    img.load()
                    img_rgb = img.convert("RGB")
            except Exception:  # noqa: BLE001 — corrupt image, drop silently
                continue
            result = pipeline.analyze(img_rgb)
            predicted = result.get("class_name", "")
            support[true_class] += 1
            if predicted == true_class:
                tp[true_class] += 1
            else:
                fn[true_class] += 1
                if predicted in known_classes:
                    fp[predicted] += 1

    per_class: dict[str, dict[str, float]] = {}
    for cls in sorted(known_classes):
        s = support[cls]
        if s == 0:
            per_class[cls] = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}
            continue
        prec_den = tp[cls] + fp[cls]
        rec_den = tp[cls] + fn[cls]
        precision = tp[cls] / prec_den if prec_den else 0.0
        recall = tp[cls] / rec_den if rec_den else 0.0
        f1_den = precision + recall
        f1 = 2 * precision * recall / f1_den if f1_den else 0.0
        per_class[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": s,
        }

    macro_f1 = round(_macro_f1(per_class), 4)
    report = {
        "macro_f1": macro_f1,
        "floor": MACRO_F1_FLOOR,
        "per_class": per_class,
        "skipped_unknown_classes": skipped_unknown_classes,
    }
    METRICS_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    classes_with_data = sum(1 for v in per_class.values() if v["support"] > 0)
    if classes_with_data == 0:
        pytest.skip(
            "no class met the MIN_IMAGES_PER_CLASS threshold; collect more smoke images"
        )

    assert macro_f1 >= MACRO_F1_FLOOR, (
        f"smoke macro-F1 {macro_f1:.3f} below floor {MACRO_F1_FLOOR}; "
        f"per-class report written to {METRICS_OUT}"
    )
