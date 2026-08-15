"""The permanent holdout's invariants, exercised against the notebook's own source.

``make_holdout`` lives in ``train_notebook.ipynb`` §2 and cannot be imported, so
these tests LIFT the real block out of the notebook and run it against a
synthetic tree. That is deliberate: a copy of the logic in the test would drift
from the notebook silently, and the one property that matters here is exactly
the kind that breaks silently.

**Membership must be stable as the corpus grows.** A ratio draw
(``make_group_split``) reshuffles when the image list changes, so "the holdout"
would be a different set of images every release and cross-run comparisons on it
would be meaningless — while still *looking* fine. The hash bucket is what makes
a row's verdict a property of the row. If someone swaps it back to a seeded
shuffle, ``test_membership_is_stable_as_the_corpus_grows`` is what says so.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / "train_notebook.ipynb"
CLASSES = ["healthy", "leaf_spot", "not_a_plant"]


def _load_block(tmp_path: Path) -> dict:
    """Exec the notebook's holdout block with a synthetic environment."""
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    nb = json.loads(NOTEBOOK.read_text())
    src = next((s for s in ("".join(c["source"]) for c in nb["cells"])
                if "HOLDOUT_PERMILLE" in s and "def make_holdout" in s), None)
    if src is None:
        pytest.fail("holdout block not found in the notebook — was it renamed?")
    block = src[src.index("HOLDOUT_PERMILLE ="):src.index("def make_val_realworld(")]

    train_root, holdout = tmp_path / "train", tmp_path / "holdout"

    def iter_images(root):
        return (p for p in sorted(Path(root).rglob("*")) if p.suffix == ".jpg")

    def read_sidecar(p):
        q = p.with_suffix(".json")
        return json.loads(q.read_text()) if q.exists() else None

    def count_images(root):
        return sum(1 for _ in iter_images(root)) if Path(root).exists() else 0

    ns = {"CLASSES": CLASSES, "TRAIN_ROOT": train_root, "HOLDOUT": holdout,
          "iter_images": iter_images, "read_sidecar": read_sidecar,
          "count_images": count_images, "hashlib": hashlib, "shutil": shutil,
          "random": random, "Path": Path}
    exec(compile(block, "<notebook holdout block>", "exec"), ns)
    ns["_train_root"], ns["_holdout"] = train_root, holdout
    ns["_iter_images"] = iter_images
    return ns


def _build(train_root: Path, n_per_class: int) -> None:
    """Half supplement rows, half plantdoc, so source filtering is exercised."""
    for cls in CLASSES:
        (train_root / cls).mkdir(parents=True, exist_ok=True)
        for k in range(n_per_class):
            supp = k % 2 == 0
            stem = f"{'supp' if supp else 'pd'}_{cls}_{k:04d}"
            p = train_root / cls / f"{stem}.jpg"
            p.write_bytes(b"x")
            p.with_suffix(".json").write_text(json.dumps({
                "source": "supplement" if supp else "plantdoc",
                "plant_id": stem, "subject_domain": "houseplant"}))


def _held(ns) -> set[str]:
    return {p.name for p in ns["_iter_images"](ns["_holdout"])}


def test_draws_only_from_holdout_sources(tmp_path):
    ns = _load_block(tmp_path)
    _build(ns["_train_root"], 120)
    ns["make_holdout"]()
    held = _held(ns)
    assert held, "nothing was held out"
    assert all(h.startswith("supp_") for h in held), (
        "a non-supplement source leaked into the holdout — HOLDOUT_SOURCES is not "
        "being honoured, and the ship gate just lost rows it needs")


def test_sidecars_travel_with_the_image(tmp_path):
    # Without the sidecar the row loses source / subject_domain, so the per-domain
    # holdout report silently reads 'unspecified' for everything.
    ns = _load_block(tmp_path)
    _build(ns["_train_root"], 60)
    ns["make_holdout"]()
    imgs = list(ns["_iter_images"](ns["_holdout"]))
    assert imgs
    assert all(p.with_suffix(".json").exists() for p in imgs)


def test_membership_is_stable_as_the_corpus_grows(tmp_path):
    """The property a ratio draw does not have, and the reason for the bucket."""
    ns = _load_block(tmp_path)
    _build(ns["_train_root"], 100)
    ns["make_holdout"]()
    small = _held(ns)
    assert small

    shutil.rmtree(tmp_path / "train")
    shutil.rmtree(tmp_path / "holdout")
    ns2 = _load_block(tmp_path)
    _build(ns2["_train_root"], 300)          # corpus tripled
    ns2["make_holdout"]()
    large = _held(ns2)

    assert small <= large, (
        "rows left the holdout when the corpus grew. Membership must be a property "
        "of the image, not of the draw order — otherwise every cross-run comparison "
        "on this split is comparing different images.")
    assert len(large) > len(small), "a tripled corpus should hold out more rows"


def test_rerun_is_idempotent(tmp_path):
    ns = _load_block(tmp_path)
    _build(ns["_train_root"], 80)
    ns["make_holdout"]()
    first = _held(ns)
    ns["make_holdout"]()
    assert _held(ns) == first


def test_bucket_is_not_process_salted(tmp_path):
    """sha1, not hash(). Python salts str hashes per process, which would
    reshuffle the holdout on every kernel restart — the exact failure the bucket
    exists to prevent, and one that leaves no trace in the run log."""
    ns = _load_block(tmp_path)
    got = ns["_holdout_bucket"]("supplement", "supp_healthy_0001")
    want = int.from_bytes(hashlib.sha1(b"supplement|supp_healthy_0001").digest()[:4],
                          "big") % 1000
    assert got == want
    assert 0 <= got < 1000


def test_whole_group_moves_together(tmp_path):
    """Group-awareness: one photo's crops can span classes, and splitting them
    across train/holdout is the leak every other split in §2 guards against."""
    ns = _load_block(tmp_path)
    train_root = ns["_train_root"]
    for cls in CLASSES:
        (train_root / cls).mkdir(parents=True, exist_ok=True)
    # One group_id, three files, three different classes.
    shared = "supp_shared_0001"
    for cls in CLASSES:
        p = train_root / cls / f"{shared}_{cls}.jpg"
        p.write_bytes(b"x")
        p.with_suffix(".json").write_text(json.dumps(
            {"source": "supplement", "plant_id": shared, "subject_domain": "houseplant"}))
    ns["make_holdout"](permille=1000)        # take everything, then check co-location
    assert len(_held(ns)) == len(CLASSES)
    assert not list(ns["_iter_images"](train_root))
