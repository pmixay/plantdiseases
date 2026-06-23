#!/usr/bin/env python3
"""Persistent, content-addressed frozen eval set — the fix for cross-version A/B.

Why this exists
---------------
The notebook's ``make_group_split`` (``§2``) draws the held-out splits with
``rng.shuffle(ids)`` and ``k = int(len(ids) * ratio)``. Both the order and the
count depend on the *current* dataset pool, so **the held-out eval set changes
every time the pool changes** (e.g. a new Kaggle-supplement version). That makes
``val_realworld`` a moving target: per-class numbers across two training runs are
measured on *different* images, so a point-estimate "regression" (the v2.6.6
leaf_scorch story) cannot be told apart from eval-set churn, and a *paired*
bootstrap (the only trustworthy A/B) is impossible because there is no shared
test set.

This module pins a **committed, content-addressed** set of images into
``val_realworld`` on every run. Identity is the image *content*, not its path:

* ``sha256`` of the file bytes — exact match, survives renames/moves.
* an 8×8 DCT **pHash** — fuzzy match (Hamming ≤ threshold), survives re-encode /
  re-compress / mild resize, which is how the same photo reappears across
  dataset versions.

Because the manifest lives in git and is keyed by content, the frozen set is
**persistent across the ephemeral Colab filesystem and across dataset versions**:
whatever build the notebook runs, the same photos land in eval, so
``v_prev`` vs ``v_next`` becomes a clean paired comparison on a fixed set.

Pipeline
--------
1. ``build_frozen_eval.py`` (run once, on Colab, over a built held-out folder)
   writes ``server/eval/frozen_eval_manifest.json``.
2. The notebook ``§2`` calls :func:`pin_frozen_eval` **before** ``make_val_*`` so
   the manifest images are moved out of train into ``val_realworld`` first; the
   random split then only tops up the remainder.
3. ``paired_eval.py`` evaluates two model bundles on the frozen folder.

Dependencies: ``numpy`` + ``Pillow`` only (eval/dev tooling, never the runtime
image). Pure-python identity helpers are unit-tested without a model.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
DEFAULT_PHASH_THRESHOLD = 6
_PHASH_IMG = 32  # resize side before DCT
_PHASH_LOW = 8   # low-frequency block kept (-> 64-bit hash)


# ── content identity ────────────────────────────────────────────────────────

def sha256_file(path: Path, _bufsize: int = 1 << 20) -> str:
    """SHA-256 of the file bytes (exact identity, cheap)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_bufsize):
            h.update(chunk)
    return h.hexdigest()


def _dct_basis(n: int) -> np.ndarray:
    """Orthonormal DCT-II basis matrix (n×n). Cached per size."""
    k = np.arange(n)
    basis = np.cos(np.pi * (2 * k[:, None] + 1) * k[None, :] / (2 * n))
    basis[0, :] = basis[0, :] / np.sqrt(2)
    return basis * np.sqrt(2.0 / n)


_DCT32 = _dct_basis(_PHASH_IMG)


def phash_image(img: Image.Image) -> int:
    """64-bit perceptual hash (8×8 low-frequency DCT, median threshold).

    Standard pHash: greyscale → 32×32 → 2-D DCT → keep the top-left 8×8
    low-frequency block → threshold each coefficient against the block
    median (the DC term ``[0,0]`` is excluded from the median so a uniform
    brightness shift doesn't flip every bit). Robust to JPEG re-encode and
    mild resize — the way the same photo reappears across dataset versions.
    """
    g = img.convert("L").resize((_PHASH_IMG, _PHASH_IMG), Image.BILINEAR)
    a = np.asarray(g, dtype=np.float64)
    coeffs = _DCT32 @ a @ _DCT32.T
    low = coeffs[:_PHASH_LOW, :_PHASH_LOW]
    med = np.median(low.flatten()[1:])  # drop DC from the median
    bits = (low > med).flatten()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def phash_file(path: Path) -> int:
    with Image.open(path) as im:
        return phash_image(im)


def hamming(a: int, b: int) -> int:
    return int(bin(a ^ b).count("1"))


def phash_hex(value: int) -> str:
    return f"{value:016x}"


def phash_from_hex(text: str) -> int:
    return int(text, 16)


# ── manifest ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FrozenEntry:
    sha256: str
    phash: int
    cls: str
    source: str
    orig_name: str

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "phash": phash_hex(self.phash),
            "class": self.cls,
            "source": self.source,
            "orig_name": self.orig_name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FrozenEntry":
        return cls(
            sha256=d["sha256"],
            phash=phash_from_hex(d["phash"]),
            cls=d["class"],
            source=d.get("source", "unknown"),
            orig_name=d.get("orig_name", ""),
        )


@dataclass
class FrozenManifest:
    split: str
    phash_threshold: int
    entries: list[FrozenEntry]
    meta: dict

    @classmethod
    def load(cls, path: Path) -> "FrozenManifest":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = [FrozenEntry.from_dict(e) for e in d.get("images", [])]
        return cls(
            split=d.get("split", "val_realworld"),
            phash_threshold=int(d.get("phash_threshold", DEFAULT_PHASH_THRESHOLD)),
            entries=entries,
            meta={k: v for k, v in d.items() if k != "images"},
        )

    def to_dict(self) -> dict:
        out = dict(self.meta)
        out.update({
            "split": self.split,
            "phash_threshold": self.phash_threshold,
            "count": len(self.entries),
            "images": [e.to_dict() for e in self.entries],
        })
        return out

    def save(self, path: Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def sha_index(self) -> dict[str, FrozenEntry]:
        return {e.sha256: e for e in self.entries}


# ── matching + pinning ──────────────────────────────────────────────────────

def iter_class_images(root: Path):
    """Yield ``(class_name, image_path)`` for an ImageFolder-style tree."""
    root = Path(root)
    for cls_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for img in sorted(cls_dir.iterdir()):
            if img.suffix.lower() in IMAGE_EXTS:
                yield cls_dir.name, img


@dataclass
class PinStats:
    matched_sha: int = 0
    matched_phash: int = 0
    missing: int = 0
    cross_class_skipped: int = 0
    moved_files: int = 0
    missing_entries: list[FrozenEntry] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.missing_entries is None:
            self.missing_entries = []

    @property
    def matched(self) -> int:
        return self.matched_sha + self.matched_phash

    @property
    def coverage(self) -> float:
        total = self.matched + self.missing
        return (self.matched / total) if total else 1.0


def pin_frozen_eval(
    train_root: Path,
    dest_root: Path,
    manifest: FrozenManifest | Path,
    *,
    min_coverage: float = 0.90,
    move: bool = True,
    move_sidecar: bool = True,
    verbose: bool = True,
) -> PinStats:
    """Move every manifest image out of ``train_root`` into ``dest_root``.

    Runs **before** the random ``make_group_split`` calls so the frozen set is
    guaranteed-present and identical every run, regardless of how the pool
    changed. Matching is sha256-exact first, then a same-class pHash fallback
    (Hamming ≤ ``manifest.phash_threshold``) for re-encoded reappearances.

    Fail-fast: raises if realised coverage drops below ``min_coverage`` (a
    silently-shrinking frozen set would quietly de-pair the comparison it
    exists to protect). ``missing`` entries (a source simply absent from this
    build) are tolerated up to that floor. Cross-class pHash collisions are
    skipped (never relabel an image to satisfy the manifest).
    """
    train_root = Path(train_root)
    dest_root = Path(dest_root)
    if isinstance(manifest, Path):
        manifest = FrozenManifest.load(manifest)
    stats = PinStats()
    if manifest.is_empty:
        if verbose:
            print("frozen eval: manifest empty — nothing to pin (no-op)")
        return stats

    # Index the train pool by sha (cheap). pHash is computed lazily, per class,
    # only for the entries that miss on sha — phashing the whole pool is wasteful.
    sha_to_path: dict[str, tuple[str, Path]] = {}
    by_class: dict[str, list[Path]] = {}
    for cls, img in iter_class_images(train_root):
        sha_to_path[sha256_file(img)] = (cls, img)
        by_class.setdefault(cls, []).append(img)

    _phash_cache: dict[Path, int] = {}

    def _move(cls: str, src: Path) -> None:
        out_dir = dest_root / cls
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / src.name
        if dst.exists():
            # Already pinned this run (duplicate manifest entry) — skip.
            return
        if move:
            shutil.move(str(src), str(dst))
            if move_sidecar:
                sc = src.with_suffix(".json")
                if sc.exists():
                    shutil.move(str(sc), str(dst.with_suffix(".json")))
        else:
            shutil.copyfile(src, dst)
        stats.moved_files += 1

    for entry in manifest.entries:
        hit = sha_to_path.get(entry.sha256)
        if hit is not None:
            cls, src = hit
            if cls != entry.cls:
                stats.cross_class_skipped += 1
                continue
            _move(cls, src)
            stats.matched_sha += 1
            continue
        # pHash fallback within the manifest entry's class only.
        best: tuple[int, Path] | None = None
        for cand in by_class.get(entry.cls, []):
            if cand in _phash_cache:
                ph = _phash_cache[cand]
            else:
                try:
                    ph = phash_file(cand)
                except Exception:
                    continue
                _phash_cache[cand] = ph
            d = hamming(ph, entry.phash)
            if d <= manifest.phash_threshold and (best is None or d < best[0]):
                best = (d, cand)
        if best is not None:
            _move(entry.cls, best[1])
            stats.matched_phash += 1
        else:
            stats.missing += 1
            stats.missing_entries.append(entry)

    if verbose:
        print(
            f"frozen eval: pinned {stats.matched}/{len(manifest.entries)} "
            f"(sha={stats.matched_sha} phash={stats.matched_phash} "
            f"missing={stats.missing} cross_class_skip={stats.cross_class_skipped}) "
            f"-> {dest_root}  coverage={stats.coverage:.3f}"
        )
        for e in stats.missing_entries[:10]:
            print(f"  MISSING frozen[{e.cls}] {e.orig_name} sha={e.sha256[:12]}")

    if stats.coverage < min_coverage:
        raise RuntimeError(
            f"frozen eval coverage {stats.coverage:.3f} < min {min_coverage:.3f} "
            f"({stats.missing} of {len(manifest.entries)} manifest images not found "
            f"in {train_root}). The frozen set is shrinking — refusing to run a "
            f"de-paired comparison. Re-build the manifest or restore the sources."
        )
    return stats
