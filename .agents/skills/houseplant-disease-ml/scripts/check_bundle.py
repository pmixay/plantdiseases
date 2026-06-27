#!/usr/bin/env python3
"""
check_bundle — validate a PlantVision `models/` bundle against the artefact
contract the server enforces at boot.

The server is **fail-fast**: it refuses to start if a required artefact is
missing or if `classes.json` disagrees with the trained head. There is no demo
fallback. This script catches those problems *before* you ship, mirroring the
notebook §6 ship gate's "don't deploy a half-broken bundle" stance.

It is stdlib-only by default. If `onnxruntime`/`numpy` are importable it also
cross-checks the classifier ONNX head width against `classes.json` (the single
most common boot failure).

Usage
-----
    python check_bundle.py path/to/models/
    python check_bundle.py path/to/models/ --json     # machine-readable

Exit code is 0 only when there are no FAILs (WARNs are allowed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FAIL, WARN, OK, INFO = "FAIL", "WARN", "OK", "INFO"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def add(self, level: str, msg: str) -> None:
        self.rows.append((level, msg))

    @property
    def failed(self) -> bool:
        return any(lvl == FAIL for lvl, _ in self.rows)

    def print_human(self) -> None:
        icon = {FAIL: "✗", WARN: "!", OK: "✓", INFO: "·"}
        for lvl, msg in self.rows:
            print(f"  {icon[lvl]} [{lvl:4}] {msg}")
        n_fail = sum(lvl == FAIL for lvl, _ in self.rows)
        n_warn = sum(lvl == WARN for lvl, _ in self.rows)
        verdict = "BUNDLE WILL NOT BOOT" if self.failed else "bundle satisfies the boot contract"
        print(f"\n  → {verdict}  ({n_fail} fail, {n_warn} warn)")


def _load_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def check(models_dir: Path) -> Report:
    r = Report()
    if not models_dir.is_dir():
        r.add(FAIL, f"{models_dir} is not a directory")
        return r

    def has(name: str) -> bool:
        return (models_dir / name).is_file()

    # ── Stage 1 detector (required) ──────────────────────────────
    if has("detector.onnx"):
        r.add(OK, "detector.onnx present (Stage 1)")
    elif has("detector.pt"):
        r.add(WARN, "only detector.pt present — production should ship detector.onnx")
    else:
        r.add(FAIL, "no detector artefact (detector.onnx / detector.pt) — Stage 1 cannot load")

    # ── Stage 2 classifier (required: at least one ONNX) ─────────
    has_int8, has_fp32 = has("classifier_int8.onnx"), has("classifier.onnx")
    if has_int8:
        r.add(OK, "classifier_int8.onnx present (preferred runtime artefact)")
    if has_fp32:
        r.add(OK, "classifier.onnx present (FP32)")
    if not (has_int8 or has_fp32):
        r.add(FAIL, "no classifier ONNX (classifier_int8.onnx / classifier.onnx) — Stage 2 cannot load")
    if has("classifier_fp32.onnx") and has_int8:
        r.add(WARN, "classifier_fp32.onnx AND classifier_int8.onnx both present — "
                    "fp32.onnx is the 'INT8 failed drift gate' signal; verify which you meant to ship")

    # ── classes.json (required contract) ─────────────────────────
    cj_path = models_dir / "classes.json"
    classes = _load_json(cj_path) if cj_path.is_file() else None
    if classes is None:
        r.add(FAIL, "classes.json missing or unparseable — it is the boot contract (class set, img_size, architecture)")
        return r  # nothing else cross-checks without it
    names = classes.get("class_names") or classes.get("classes")
    if not isinstance(names, list) or not names:
        r.add(FAIL, "classes.json has no class_names list")
        names = []
    else:
        r.add(OK, f"classes.json declares {len(names)} classes")
        if "not_a_plant" not in names:
            r.add(WARN, "no 'not_a_plant' class — the rejection head is expected in v2.x")
    if not (classes.get("architecture")):
        r.add(WARN, "classes.json missing 'architecture' (reported by /api/version)")
    img = (classes.get("training") or {}).get("img_size") or classes.get("img_size")
    if not img:
        r.add(WARN, "classes.json missing training.img_size")
    if not (classes.get("model_version")):
        r.add(WARN, "classes.json missing model_version (servers pin behaviour to it)")

    # ── Calibration sidecars (optional but expected for prod) ────
    temp = _load_json(models_dir / "temperature.json") if has("temperature.json") else None
    if temp is None:
        r.add(WARN, "no temperature.json — probabilities serve uncalibrated (T=1.0)")
    else:
        r.add(OK, f"temperature.json present (T={temp.get('temperature')}, source={temp.get('source')})")
    conf = _load_json(models_dir / "conformal.json") if has("conformal.json") else None
    if conf is None:
        r.add(INFO, "no conformal.json — prediction sets omitted (clients must treat as optional)")
    else:
        r.add(OK, f"conformal.json present (method={conf.get('method')}, alpha={conf.get('alpha')})")
        if temp and conf.get("fit_source") and temp.get("source") and conf["fit_source"] != temp["source"]:
            r.add(WARN, f"conformal.fit_source={conf['fit_source']} != temperature.source={temp['source']} "
                        "(calibration fit on a different numerics path than what ships)")

    # ── OOD memory bank consistency ──────────────────────────────
    ood = (classes.get("ood") or {}).get("embedding")
    if ood:
        bank = ood.get("bank_file", "ood_embedding.npz")
        if has(bank):
            r.add(OK, f"OOD embedding bank present ({bank}, k={ood.get('k')}, threshold={ood.get('threshold')})")
        else:
            r.add(FAIL, f"classes.json declares ood.embedding but bank file '{bank}' is missing")
    else:
        r.add(INFO, "no embedding OOD bank declared — OOD falls back to classes.json ood.max_logit_floor")

    # ── Analysis artefacts (non-blocking) ────────────────────────
    for name, note in (("model_card.json", "provenance + benchmarks + CIs"),
                       ("training_metrics.json", "per-class recall + confusion matrices")):
        r.add(OK if has(name) else INFO, f"{name} {'present' if has(name) else 'absent'} ({note})")

    # ── Optional deep check: ONNX head width vs classes.json ─────
    if names:
        _check_head_width(models_dir, has_int8, has_fp32, len(names), r)
    return r


def _check_head_width(models_dir: Path, has_int8: bool, has_fp32: bool, n_classes: int, r: Report) -> None:
    try:
        import numpy as np  # noqa: F401
        import onnxruntime as ort
    except Exception:
        r.add(INFO, "onnxruntime/numpy not importable — skipped ONNX head-width cross-check")
        return
    art = "classifier_int8.onnx" if has_int8 else ("classifier.onnx" if has_fp32 else None)
    if art is None:
        return
    try:
        sess = ort.InferenceSession(str(models_dir / art), providers=["CPUExecutionProvider"])
        out = sess.get_outputs()[0]
        width = out.shape[-1] if out.shape else None
        if isinstance(width, int):
            if width == n_classes:
                r.add(OK, f"{art} logits width {width} matches classes.json ({n_classes})")
            else:
                r.add(FAIL, f"{art} logits width {width} != classes.json class count {n_classes} — boot will abort")
        else:
            r.add(INFO, f"{art} logits dim is dynamic/unknown — could not verify against classes.json")
    except Exception as e:  # pragma: no cover
        msg = str(e).splitlines()[0][:80]
        r.add(WARN, f"could not load {art} for head-width check ({msg}) — is the ONNX valid?")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate a PlantVision models/ bundle against the boot contract.")
    ap.add_argument("models_dir", type=Path)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()
    rep = check(args.models_dir)
    if args.json:
        print(json.dumps({"failed": rep.failed,
                          "rows": [{"level": lvl, "msg": m} for lvl, m in rep.rows]}, indent=2))
    else:
        print(f"\nChecking bundle: {args.models_dir}\n")
        rep.print_human()
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()
