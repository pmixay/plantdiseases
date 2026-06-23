#!/usr/bin/env python3
"""Paired model A/B on ONE fixed eval set — the trustworthy regression test.

The point-estimate per-class numbers in two ``training_metrics.json`` files are
NOT comparable: each run's ``val_realworld`` is a different draw. The only honest
way to ask "did the candidate regress leaf_scorch?" is to run **both** model
bundles over the **same** images and paired-bootstrap the difference
(``tools/eval_bootstrap.py``: a delta is real only when its paired CI excludes
zero).

This harness does exactly that, faithfully:

* Inference goes through the production ``server.classifier.DiseaseClassifier``,
  so preprocessing (aspect-preserving resize ratio, ImageNet norm, hflip-TTA
  logit averaging) and class order are byte-identical to what ships. Both models
  see the *same decoded image* per item.
* It runs the **Stage-2 classifier only** (no detector / blur / OOD gates) — that
  matches the notebook's ``val_realworld`` macro-F1 ship-gate path and isolates
  the head, the thing under investigation.
* On top of the paired bootstrap it computes a **deep benchmark**: per-class
  confusion-flow (where each true class's mass lands in A vs B and how it
  *moved*), a correct→wrong / wrong→correct flip analysis (McNemar), and
  per-class healthy-leak — so a real regression is not just flagged but located.

The eval set is any ImageFolder tree (``<class>/<image>``) you point it at.

Two modes:
  * two bundles:        ``--bundle-a DIR --bundle-b DIR``  (cross-version A/B)
  * quantization drift: ``--int8-vs-fp32 DIR``            (within-bundle, clean)

CLI
---
    python -m tools.paired_eval \\
        --eval-root /path/to/eval_imagefolder \\
        --bundle-a models_prev --label-a v2.6.5 --classifier-a fp32 \\
        --bundle-b models_new  --label-b v2.6.7 --classifier-b fp32 \\
        --out-json report.json --out-md report.md --focus leaf_scorch_underwatering
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from classifier import DiseaseClassifier  # noqa: E402
from tools import eval_bootstrap as eb  # noqa: E402

NON_DISEASE = ("healthy", "not_a_plant")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def iter_class_images(root: Path):
    """Yield ``(class_name, image_path)`` for an ImageFolder-style tree."""
    root = Path(root)
    for cls_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for img in sorted(cls_dir.iterdir()):
            if img.suffix.lower() in IMAGE_EXTS:
                yield cls_dir.name, img


# ── model loading (force a specific classifier variant) ─────────────────────

def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        import shutil
        shutil.copyfile(src, dst)


def load_classifier(bundle: Path, variant: str, tmp: Path) -> DiseaseClassifier:
    """Load a bundle's classifier, forcing fp32 / int8 / auto.

    ``DiseaseClassifier`` resolves ``classifier_int8.onnx`` before
    ``classifier.onnx`` from the model dir, so to pick a variant we stage a
    tiny dir containing only ``classes.json`` + the chosen graph.
    """
    bundle = Path(bundle)
    classes = bundle / "classes.json"
    if not classes.exists():
        raise FileNotFoundError(f"{bundle} has no classes.json")
    if variant == "auto":
        return DiseaseClassifier(bundle / "classifier.onnx")

    stage = tmp / f"{bundle.name}_{variant}"
    stage.mkdir(parents=True, exist_ok=True)
    _link_or_copy(classes, stage / "classes.json")
    if variant == "fp32":
        src = bundle / "classifier.onnx"
        if not src.exists() or src.stat().st_size < 40_000_000:
            # Some bundles name the fp32 graph explicitly.
            alt = bundle / "classifier_fp32.onnx"
            src = alt if alt.exists() else src
        _link_or_copy(src, stage / "classifier.onnx")
    elif variant == "int8":
        src = bundle / "classifier_int8.onnx"
        if not src.exists():
            raise FileNotFoundError(f"{bundle} has no classifier_int8.onnx")
        _link_or_copy(src, stage / "classifier_int8.onnx")
    else:
        raise ValueError(f"unknown variant {variant!r}")
    return DiseaseClassifier(stage / "classifier.onnx")


# ── eval set + prediction ───────────────────────────────────────────────────

def discover(eval_root: Path, class_names: list[str]) -> tuple[list[Path], np.ndarray]:
    """Collect ``(paths, y_true)`` for images whose class folder is known."""
    idx = {c: i for i, c in enumerate(class_names)}
    paths: list[Path] = []
    labels: list[int] = []
    for cls, img in iter_class_images(eval_root):
        if cls in idx and img.suffix.lower() in IMAGE_EXTS:
            paths.append(img)
            labels.append(idx[cls])
    if not paths:
        raise RuntimeError(
            f"no images under {eval_root} matching the model's classes "
            f"({class_names}). Expected <class>/<image> layout."
        )
    return paths, np.asarray(labels, dtype=np.int64)


def predict_pair(
    paths: list[Path],
    y_true: np.ndarray,
    clf_a: DiseaseClassifier,
    clf_b: DiseaseClassifier,
    class_names: list[str],
    *,
    batch: int = 32,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run BOTH classifiers over the same decoded images.

    Decodes each image once and feeds the identical PIL object to both models,
    so the only difference is the weights. Unreadable images are dropped from
    BOTH so the three returned vectors stay aligned.
    """
    idx = {c: i for i, c in enumerate(class_names)}
    yt: list[int] = []
    ya: list[int] = []
    yb: list[int] = []
    n = len(paths)
    for start in range(0, n, batch):
        chunk = paths[start:start + batch]
        labs = y_true[start:start + batch]
        imgs: list[Image.Image] = []
        keep_labs: list[int] = []
        for p, lab in zip(chunk, labs, strict=True):
            try:
                im = Image.open(p).convert("RGB")
                im.load()
            except Exception as e:  # noqa: BLE001
                print(f"  skip unreadable {p}: {e}")
                continue
            imgs.append(im)
            keep_labs.append(int(lab))
        if not imgs:
            continue
        ra = clf_a.classify_batch(imgs)
        rb = clf_b.classify_batch(imgs)
        for lab, da, db in zip(keep_labs, ra, rb, strict=True):
            yt.append(lab)
            ya.append(idx[da["class_name"]])
            yb.append(idx[db["class_name"]])
        done = min(start + batch, n)
        print(f"  predicted {done}/{n}", end="\r", flush=True)
    print()
    return (
        np.asarray(yt, dtype=np.int64),
        np.asarray(ya, dtype=np.int64),
        np.asarray(yb, dtype=np.int64),
    )


# ── deep benchmark (where do the regressions go?) ───────────────────────────

def confusion_flow(
    y_true: np.ndarray, y_pred_a: np.ndarray, y_pred_b: np.ndarray,
    class_names: list[str], true_cls: str,
) -> dict:
    """For one true class, the prediction distribution under A and B + the move.

    Shows whether B's loss on ``true_cls`` leaks to ``healthy`` (under-calling)
    or to another disease (confusion) — the difference between the leaf_scorch
    "generic-healthy-negatives" story and a representation problem.
    """
    ci = class_names.index(true_cls)
    mask = y_true == ci
    n = int(mask.sum())
    if n == 0:
        return {"true_class": true_cls, "support": 0}
    a = y_pred_a[mask]
    b = y_pred_b[mask]
    flow = {}
    for j, name in enumerate(class_names):
        ca, cb = int((a == j).sum()), int((b == j).sum())
        if ca or cb:
            flow[name] = {"a": ca, "b": cb, "delta_b_minus_a": cb - ca}
    return {
        "true_class": true_cls,
        "support": n,
        "correct_a": int((a == ci).sum()),
        "correct_b": int((b == ci).sum()),
        "to_healthy_a": int((a == class_names.index("healthy")).sum())
        if "healthy" in class_names else None,
        "to_healthy_b": int((b == class_names.index("healthy")).sum())
        if "healthy" in class_names else None,
        "flow": flow,
    }


def flip_analysis(
    y_true: np.ndarray, y_pred_a: np.ndarray, y_pred_b: np.ndarray,
    class_names: list[str],
) -> dict:
    """McNemar-style correctness flips, overall and per true class."""
    corr_a = y_pred_a == y_true
    corr_b = y_pred_b == y_true
    b_only = int((corr_a & ~corr_b).sum())   # A right, B wrong  (regressions)
    c_only = int((~corr_a & corr_b).sum())   # A wrong, B right  (gains)
    per_class = {}
    for i, name in enumerate(class_names):
        m = y_true == i
        if not m.any():
            continue
        per_class[name] = {
            "support": int(m.sum()),
            "regressed_a_right_b_wrong": int((m & corr_a & ~corr_b).sum()),
            "gained_a_wrong_b_right": int((m & ~corr_a & corr_b).sum()),
            "acc_a": round(float(corr_a[m].mean()), 4),
            "acc_b": round(float(corr_b[m].mean()), 4),
        }
    # Two-sided exact McNemar p on the discordant pairs (binomial, p=0.5).
    from math import comb
    nd = b_only + c_only
    k = min(b_only, c_only)
    p = min(1.0, 2.0 * sum(comb(nd, i) for i in range(k + 1)) / (2 ** nd)) if nd else 1.0
    return {
        "a_right_b_wrong": b_only,
        "a_wrong_b_right": c_only,
        "net_b_minus_a": c_only - b_only,
        "mcnemar_p_two_sided": round(p, 5),
        "overall_acc_a": round(float(corr_a.mean()), 4),
        "overall_acc_b": round(float(corr_b.mean()), 4),
        "per_class": per_class,
    }


def healthy_bias(y_true, y_pred, class_names) -> float:
    """Disease images predicted healthy / total disease images (lower=better)."""
    if "healthy" not in class_names:
        return float("nan")
    hi = class_names.index(healthy_name(class_names))
    non = {class_names.index(c) for c in NON_DISEASE if c in class_names}
    dmask = ~np.isin(y_true, list(non))
    d = int(dmask.sum())
    return float((y_pred[dmask] == hi).sum() / d) if d else float("nan")


def healthy_name(class_names):
    return "healthy"


# ── orchestration ───────────────────────────────────────────────────────────

def run(
    eval_root: Path, clf_a, clf_b, label_a: str, label_b: str,
    *, n_boot: int, ci: float, seed: int, focus: list[str], batch: int,
) -> dict:
    if list(clf_a.CLASS_NAMES) != list(clf_b.CLASS_NAMES):
        raise RuntimeError(
            "the two bundles have different class lists — not comparable:\n"
            f"  {label_a}: {clf_a.CLASS_NAMES}\n  {label_b}: {clf_b.CLASS_NAMES}"
        )
    class_names = list(clf_a.CLASS_NAMES)
    paths, y_true_all = discover(eval_root, class_names)
    print(f"eval set: {len(paths)} images under {eval_root}")
    y_true, y_a, y_b = predict_pair(
        paths, y_true_all, clf_a, clf_b, class_names, batch=batch
    )
    print(f"scored {len(y_true)} images")

    paired_f1 = eb.paired_bootstrap(
        y_true, y_a, y_b, class_names, n_boot=n_boot, ci=ci, seed=seed)
    paired_rec = eb.paired_per_class_recall(
        y_true, y_a, y_b, class_names, n_boot=n_boot, ci=ci, seed=seed)
    paired_prec = eb.paired_per_class_precision(
        y_true, y_a, y_b, class_names, n_boot=n_boot, ci=ci, seed=seed)

    focus = [c for c in focus if c in class_names]
    flows = {c: confusion_flow(y_true, y_a, y_b, class_names, c) for c in focus}
    flips = flip_analysis(y_true, y_a, y_b, class_names)

    return {
        "eval_root": str(eval_root),
        "label_a": label_a,
        "label_b": label_b,
        "class_names": class_names,
        "n_scored": int(len(y_true)),
        "n_boot": n_boot,
        "ci_level": ci,
        "healthy_bias_a": round(healthy_bias(y_true, y_a, class_names), 4),
        "healthy_bias_b": round(healthy_bias(y_true, y_b, class_names), 4),
        "disease_macro_f1": paired_f1,
        "per_class_recall": paired_rec,
        "per_class_precision": paired_prec,
        "confusion_flow": flows,
        "flip_analysis": flips,
        # raw predictions so the result is reproducible / re-analysable offline
        "y_true": y_true.tolist(),
        "y_pred_a": y_a.tolist(),
        "y_pred_b": y_b.tolist(),
    }


def _sig(d: dict) -> str:
    lo, hi = d.get("diff_ci_low"), d.get("diff_ci_high")
    if lo is None or hi is None or lo != lo:  # nan
        return "n/a"
    return "REAL" if d.get("significant") else "noise"


def to_markdown(rep: dict) -> str:
    a, b = rep["label_a"], rep["label_b"]
    f = rep["disease_macro_f1"]
    L = []
    L.append(f"# Paired eval — {a} (A) vs {b} (B)")
    L.append("")
    L.append(f"- Eval set: `{rep['eval_root']}`  ·  n = **{rep['n_scored']}**  ·  "
             f"bootstrap = {rep['n_boot']}  ·  CI = {int(rep['ci_level']*100)}%")
    L.append("- Stage-2 classifier only (no detector/gates), hflip-TTA per bundle.")
    L.append("")
    L.append("## Disease macro-F1 (paired)")
    L.append(f"- A `{a}` = **{f['f1_a']:.4f}**   B `{b}` = **{f['f1_b']:.4f}**")
    L.append(f"- diff (A−B) = **{f['diff_point']:+.4f}**  "
             f"95% CI [{f['diff_ci_low']:+.4f}, {f['diff_ci_high']:+.4f}]  "
             f"→ **{'REAL difference' if f['significant'] else 'NOT significant (noise)'}**")
    L.append(f"- healthy-bias: A {rep['healthy_bias_a']}  ·  B {rep['healthy_bias_b']}")
    L.append("")
    L.append("## Per-class paired deltas (A−B; positive ⇒ B regressed)")
    L.append("")
    L.append("| class | recall A→B | Δrec [CI] | rec | precision A→B | Δprec [CI] | prec | n |")
    L.append("|---|---|---|:--:|---|---|:--:|--:|")
    rc = rep["per_class_recall"]["classes"]
    pc = rep["per_class_precision"]["classes"]
    for c in rep["class_names"]:
        r, p = rc[c], pc[c]
        rrow = (f"{r['recall_a']:.3f}→{r['recall_b']:.3f}" if r["recall_a"] == r["recall_a"]
                else "—")
        rci = (f"[{r['diff_ci_low']:+.3f},{r['diff_ci_high']:+.3f}]"
               if r.get("diff_ci_low") == r.get("diff_ci_low") else "—")
        prow = (f"{p['precision_a']:.3f}→{p['precision_b']:.3f}"
                if p["precision_a"] == p["precision_a"] else "—")
        pci = (f"[{p['diff_ci_low']:+.3f},{p['diff_ci_high']:+.3f}]"
               if p.get("diff_ci_low") == p.get("diff_ci_low") else "—")
        L.append(f"| {c} | {rrow} | {rci} | {_sig(r)} | {prow} | {pci} | {_sig(p)} | "
                 f"{r.get('support', 0)} |")
    L.append("")
    # explicit verdict lines
    reg = []
    for c in rep["class_names"]:
        r, p = rc[c], pc[c]
        if r.get("significant") and r.get("diff_point", 0) > 0:
            reg.append(f"  - **{c} recall** regressed {r['recall_a']:.3f}→{r['recall_b']:.3f} "
                       f"(REAL, CI [{r['diff_ci_low']:+.3f},{r['diff_ci_high']:+.3f}])")
        if p.get("significant") and p.get("diff_point", 0) > 0:
            reg.append(f"  - **{c} precision** regressed {p['precision_a']:.3f}→{p['precision_b']:.3f} "
                       f"(REAL, CI [{p['diff_ci_low']:+.3f},{p['diff_ci_high']:+.3f}])")
    L.append("## Verdict — statistically real regressions (B worse than A)")
    L.extend(reg if reg else ["  - none: every per-class delta CI straddles 0 (noise)."])
    L.append("")
    fl = rep["flip_analysis"]
    L.append("## Correctness flips (McNemar)")
    L.append(f"- A-right/B-wrong = {fl['a_right_b_wrong']}  ·  "
             f"A-wrong/B-right = {fl['a_wrong_b_right']}  ·  "
             f"net (B−A) = **{fl['net_b_minus_a']:+d}**  ·  p = {fl['mcnemar_p_two_sided']}")
    L.append(f"- overall acc: A {fl['overall_acc_a']}  ·  B {fl['overall_acc_b']}")
    L.append("")
    if rep["confusion_flow"]:
        L.append("## Confusion-flow on focus classes (where did B's mass go?)")
        for c, fw in rep["confusion_flow"].items():
            if not fw.get("support"):
                L.append(f"- `{c}`: no support in eval set")
                continue
            L.append(f"- `{c}` (n={fw['support']}): correct A={fw['correct_a']} → "
                     f"B={fw['correct_b']}; →healthy A={fw['to_healthy_a']} → "
                     f"B={fw['to_healthy_b']}")
            moves = sorted(fw["flow"].items(),
                           key=lambda kv: kv[1]["delta_b_minus_a"])
            for name, m in moves:
                if m["delta_b_minus_a"] != 0:
                    L.append(f"    - {name}: A={m['a']} B={m['b']} "
                             f"(Δ {m['delta_b_minus_a']:+d})")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-root", type=Path, required=True)
    ap.add_argument("--bundle-a", type=Path)
    ap.add_argument("--bundle-b", type=Path)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--classifier-a", choices=["auto", "fp32", "int8"], default="fp32")
    ap.add_argument("--classifier-b", choices=["auto", "fp32", "int8"], default="fp32")
    ap.add_argument("--int8-vs-fp32", type=Path, default=None,
                    help="within-bundle quantization drift: A=fp32, B=int8 of this bundle")
    ap.add_argument("--focus", nargs="*",
                    default=["leaf_scorch_underwatering", "nutrient_deficiency",
                             "gray_mold_botrytis"])
    ap.add_argument("--n-boot", type=int, default=5000)
    ap.add_argument("--ci", type=float, default=0.95)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--out-md", type=Path, default=None)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if args.int8_vs_fp32 is not None:
            bundle = args.int8_vs_fp32
            clf_a = load_classifier(bundle, "fp32", tmp)
            clf_b = load_classifier(bundle, "int8", tmp)
            label_a = args.label_a if args.label_a != "A" else "fp32"
            label_b = args.label_b if args.label_b != "B" else "int8"
        else:
            if not args.bundle_a or not args.bundle_b:
                ap.error("provide --bundle-a and --bundle-b (or --int8-vs-fp32)")
            clf_a = load_classifier(args.bundle_a, args.classifier_a, tmp)
            clf_b = load_classifier(args.bundle_b, args.classifier_b, tmp)
            label_a, label_b = args.label_a, args.label_b

        rep = run(
            args.eval_root, clf_a, clf_b, label_a, label_b,
            n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            focus=args.focus, batch=args.batch,
        )

    md = to_markdown(rep)
    print("\n" + md)
    if args.out_json:
        args.out_json.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"wrote {args.out_json}")
    if args.out_md:
        args.out_md.write_text(md, encoding="utf-8")
        print(f"wrote {args.out_md}")


if __name__ == "__main__":
    main()
