#!/usr/bin/env python3
"""Benchmark the shipped INT8 ONNX classifier against the FP32 graph.

Mirrors ``classifier.py``'s production ORT session config exactly
(intra_op=1, inter_op=1, ORT_ENABLE_ALL, CPUExecutionProvider) so the
numbers reflect what the server would actually see, then reports p50/p90/p99
single-image latency, batched throughput, peak RSS, and a ship recommendation.

Usage:
    python tools/benchmark_backends.py --model-dir /path/to/models \\
        [--iters 300] [--warmup 30] [--batch 8] [--seed 0]

No model accuracy is computed here (that lives in the notebook's §6 ship
gate); this answers only "which artifact is faster/smaller on THIS CPU".
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    raise SystemExit("onnxruntime is required: pip install onnxruntime")


def _peak_rss_mb() -> float | None:
    """Best-effort peak resident set size in MiB (Linux/macOS)."""
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes.
        return ru / 1024.0 if platform.system() == "Linux" else ru / (1024.0 * 1024.0)
    except Exception:
        return None


def _make_session(path: Path) -> ort.InferenceSession:
    # Identical to DiseaseClassifier._load_onnx so latency reflects prod.
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(
        str(path), sess_options=opts, providers=["CPUExecutionProvider"],
    )


def _percentiles(times_ms: list[float]) -> dict[str, float]:
    a = np.asarray(times_ms, dtype=np.float64)
    return {
        "mean": float(a.mean()),
        "p50": float(np.percentile(a, 50)),
        "p90": float(np.percentile(a, 90)),
        "p99": float(np.percentile(a, 99)),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def bench_one(
    path: Path, *, img_size: int, iters: int, warmup: int,
    batch: int, rng: np.random.Generator,
) -> dict:
    sess = _make_session(path)
    inp = sess.get_inputs()[0]
    out = sess.get_outputs()[0]
    name = inp.name

    # Random NCHW float32 input in the normalized range the model expects.
    def gen(n: int) -> np.ndarray:
        return rng.standard_normal((n, 3, img_size, img_size)).astype(np.float32)

    x1 = gen(1)
    for _ in range(warmup):
        sess.run(None, {name: x1})

    single = []
    for _ in range(iters):
        x = gen(1)
        t0 = time.perf_counter()
        sess.run(None, {name: x})
        single.append((time.perf_counter() - t0) * 1e3)

    batched = None
    if batch > 1:
        xb = gen(batch)
        for _ in range(max(5, warmup // 3)):
            sess.run(None, {name: xb})
        bt = []
        for _ in range(max(20, iters // 5)):
            xb = gen(batch)
            t0 = time.perf_counter()
            sess.run(None, {name: xb})
            bt.append((time.perf_counter() - t0) * 1e3)
        ba = np.asarray(bt)
        batched = {
            "batch": batch,
            "ms_per_batch_p50": float(np.percentile(ba, 50)),
            "ms_per_image_p50": float(np.percentile(ba, 50)) / batch,
            "images_per_sec": batch * 1000.0 / float(np.percentile(ba, 50)),
        }

    out_dim = next((d for d in out.shape if isinstance(d, int)), None)
    return {
        "artifact": path.name,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "output_dim": out_dim,
        "single_image_ms": _percentiles(single),
        "batched": batched,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--warmup", type=int, default=30)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--img-size", type=int, default=None,
                    help="override; else read from classes.json")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    md = args.model_dir
    img_size = args.img_size
    if img_size is None:
        cj = md / "classes.json"
        if cj.exists():
            img_size = int(
                (json.loads(cj.read_text()).get("training") or {}).get("img_size", 256)
            )
        else:
            img_size = 256

    fp32 = md / "classifier.onnx"
    int8 = md / "classifier_int8.onnx"
    present = [(p) for p in (int8, fp32) if p.exists()]
    if not present:
        raise SystemExit(f"No classifier_int8.onnx / classifier.onnx in {md}")

    rng = np.random.default_rng(args.seed)
    results = []
    for p in present:
        print(f"benchmarking {p.name} ...", flush=True)
        results.append(
            bench_one(p, img_size=img_size, iters=args.iters,
                      warmup=args.warmup, batch=args.batch, rng=rng)
        )

    report = {
        "host": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "onnxruntime": ort.__version__,
            "available_providers": ort.get_available_providers(),
            "numpy": np.__version__,
        },
        "config": {
            "img_size": img_size, "iters": args.iters,
            "warmup": args.warmup, "batch": args.batch, "seed": args.seed,
            "intra_op_threads": 1, "inter_op_threads": 1,
        },
        "results": results,
        "peak_rss_mb": _peak_rss_mb(),
    }

    # Ship recommendation: compare single-image p50 of int8 vs fp32.
    by_name = {r["artifact"]: r for r in results}
    rec = None
    if "classifier_int8.onnx" in by_name and "classifier.onnx" in by_name:
        i8 = by_name["classifier_int8.onnx"]["single_image_ms"]["p50"]
        f3 = by_name["classifier.onnx"]["single_image_ms"]["p50"]
        i8_sz = by_name["classifier_int8.onnx"]["size_mb"]
        f3_sz = by_name["classifier.onnx"]["size_mb"]
        faster = "int8" if i8 < f3 else "fp32"
        lat_delta_pct = (i8 - f3) / f3 * 100.0
        disk_save_pct = (f3_sz - i8_sz) / f3_sz * 100.0
        if faster == "fp32":
            verdict = (
                f"FP32 is faster here by {lat_delta_pct:+.1f}% p50 latency. "
                f"INT8 saves {disk_save_pct:.0f}% disk ({f3_sz}->{i8_sz} MB). "
                "Ship FP32 unless memory-bound; this CPU lacks int8 accel."
            )
        else:
            verdict = (
                f"INT8 is faster here by {-lat_delta_pct:.1f}% p50 latency "
                f"AND saves {disk_save_pct:.0f}% disk. Ship INT8."
            )
        rec = {
            "faster": faster,
            "int8_p50_ms": round(i8, 3),
            "fp32_p50_ms": round(f3, 3),
            "int8_latency_vs_fp32_pct": round(lat_delta_pct, 1),
            "disk_saving_pct": round(disk_save_pct, 1),
            "verdict": verdict,
        }
    report["recommendation"] = rec

    print(json.dumps(report, indent=2))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
