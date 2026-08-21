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


def _cells() -> dict[int, str]:
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    nb = json.loads(NOTEBOOK.read_text())
    return {i: "".join(c["source"]) for i, c in enumerate(nb["cells"])
            if c["cell_type"] == "code"}


def test_every_code_cell_parses():
    """The cheapest possible guard against shipping a SyntaxError to an A100 run
    that only reaches §6 after an hour of training."""
    import ast
    for i, src in _cells().items():
        try:
            ast.parse(src)
        except SyntaxError as e:
            pytest.fail(f"cell {i} does not parse: {e}")


def test_holdout_is_drawn_before_the_ratio_splits():
    """Order is the guarantee. A holdout populated after val_realworld /
    val_indomain / the two calibration splits is a holdout of whatever they did
    not want, and its rows would already have leaked into calibration."""
    cells = _cells()
    c6 = next(s for s in cells.values() if "def make_holdout(" in s)
    assert c6.index("make_holdout()") < c6.index("make_val_realworld();")
    c2 = next(s for s in cells.values() if "SPLIT_ROOTS = (" in s)
    # In SPLIT_ROOTS so §3's dedup / exact-leak / pHash-leak guards cover it too.
    roots = c2[c2.index("SPLIT_ROOTS = ("):c2.index("SPLIT_ROOTS = (") + 200]
    assert "HOLDOUT" in roots


def test_benchmark_contaminated_sources_are_denied():
    """`aditisalvi04`'s Hibiscus folders re-publish the OOD benchmark's own
    photographs (60/60 sampled at pHash Hamming <= 6). Training on them would
    destroy the only never-trained eval set, and nothing downstream would notice
    — the numbers would simply improve."""
    c6 = next(s for s in _cells().values() if "KAGGLE_DENY_FOLDERS = {" in s)
    start = c6.index("KAGGLE_DENY_FOLDERS = {")
    block = c6[start:c6.index("    return True", start) + len("    return True")]
    ns: dict = {}
    exec(compile(block, "<deny-list>", "exec"), ns)
    allowed = ns["_assert_source_allowed"]

    assert allowed("itselif/ornamental-plant-diseases-master-dataset", "rose_healthy")
    assert allowed("aditisalvi04/indoor-plant-disease-dataset", "aloe_sunburn")
    for slug, folder in [
        ("shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update", ""),
        ("aditisalvi04/indoor-plant-disease-dataset", "hibiscus_scorch"),
        ("aditisalvi04/indoor-plant-disease-dataset", "hibiscus_healthy"),
    ]:
        with pytest.raises(RuntimeError, match="BENCHMARK CONTAMINATION"):
            allowed(slug, folder)
    # And it must sit at the CHOKE POINT, not in one fetcher. There are six
    # `_kaggle_download(slug)` call sites; checking inside that function is what
    # makes a contaminated slug unusable rather than merely un-wired.
    dl = c6.index("def _kaggle_download(slug):")
    body = c6[dl:c6.index("\ndef ", dl + 1)]
    assert "_assert_source_allowed(slug)" in body, (
        "the slug deny-list is not inside _kaggle_download — a contaminated slug "
        "added to any other fetcher would be pulled with no check")
    # Folder-level hits must EXCLUDE the folder, not kill the dataset: the rest
    # of aditisalvi04 is clean supply and the error message says so itself.
    fn = c6.index("def _orn_map_folder(")
    fn_body = c6[fn:c6.index("\ndef ", fn + 1)]
    assert "return None" in fn_body and "except RuntimeError" in fn_body
    # …and matched against the relative path, so `Hibiscus/Scorch/` (leaf name
    # 'scorch') cannot slip past a deny entry written as 'hibiscus_scorch'.
    assert "rel_norm" in fn_body


def test_class_logit_bias_folds_before_calibration():
    """A bias applied after temperature/conformal are fitted leaves the
    prediction sets claiming a coverage guarantee they no longer have. It must
    land in the head's bias vector before the export, so everything downstream
    is fitted on the biased logits."""
    c14 = next(s for s in _cells().values() if "CLASS_LOGIT_BIAS_APPLIED" in s)
    fold = c14.index("CLASS_LOGIT_BIAS_APPLIED = {}")
    assert fold < c14.index("torch.onnx.export(")
    assert fold < c14.index("eval_sess = ")          # temperature + conformal read this
    assert c14.count("_final.bias[CLASS_NAMES.index(_cname)] +=") == 1
    # classes.json must record it as provenance, never as an instruction to
    # re-apply at serving time (that would double the shift).
    assert "Recorded for traceability only" in c14


def test_stage1_audit_runs_in_every_mode():
    """The audit is the thing that would have caught the detector lottery. It
    must not sit behind RUN_DETECTOR_CROP_EVAL, which is off for `combined` —
    the mode that ships."""
    c14 = next(s for s in _cells().values() if "_stage1_boxes = None" in s)
    assert c14.index("_stage1_boxes = None") < c14.index("RUN_DETECTOR_CROP_EVAL =")
    assert c14.count("_det.predict(") == 1, "the crop eval should reuse the audit's boxes"


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


# ── 2.7.3: regime-aware sampling + the calibration arithmetic ──────────────

def _section_source(marker: str) -> str:
    """Source of the notebook cell containing ``marker``.

    Located by content rather than by index: cell numbers shift whenever a cell
    is added, and a test that silently reads the wrong cell passes for the wrong
    reason.
    """
    if not NOTEBOOK.exists():
        pytest.skip("train_notebook.ipynb not present")
    nb = json.loads(NOTEBOOK.read_text())
    for cell in nb["cells"]:
        src = "".join(cell["source"])
        if marker in src:
            return src
    pytest.fail(f"no notebook cell contains {marker!r} — was it renamed?")

def test_regime_multiplier_preserves_the_class_draw_share():
    """The 2.7.3 sampler change must move WHICH scorch images are drawn, not
    how much scorch is drawn.

    Boosting a quarter of a class 3x without normalising also raises the whole
    class's draw share ~1.5x, which would (a) make the run untestable — a gain
    could not be attributed between "the dull stratum transfers" and "more
    scorch overall" — and (b) silently desynchronise the LogitAdjustment prior,
    which assumes log_prior matches the batch distribution it is trained under.
    """
    src = _section_source("def regime_multipliers")
    assert "scale = len(rows) / float(sum(mult[i] for i in rows))" in src, \
        "the per-class normalisation is gone; the sampler now moves the class prior too"
    assert "regime_mult=_regime_mult" in src, "the multiplier is computed but not passed"
    assert "REGIME_UPWEIGHT_CLASSES" in _section_source("MODEL_VERSION ="), "the knob left §1"


def test_regime_threshold_is_derived_not_hardcoded():
    """A fixed saturation cut goes stale as the corpus changes; the class's own
    quantile does not."""
    src = _section_source("def regime_multipliers")
    assert "np.quantile(sat, quantile)" in src, \
        "the low-chroma threshold must be the class's own quantile, computed at run time"


def test_conformal_regime_audit_does_not_ship_its_own_q_hat():
    """The stratum-balanced q_hat is REPORTED, never applied. Changing q_hat
    changes every served prediction set and deserves its own A/B rather than
    arriving as a side effect of adding a diagnostic."""
    src = _section_source("regime_audit")
    assert "stratum_balanced_q_hat" in src, "the regime conformal audit is gone"
    assert "cfg['q_hat'] = _q_bal" not in src, \
        "the balanced q_hat is being shipped; it is a diagnostic, not a release change"


def test_temperature_is_not_claimed_to_move_predictions():
    """argmax(z/T) == argmax(z) for every T > 0. Guard the comment that says so,
    because 'recalibrate temperature to fix recall' is the plausible-sounding
    wrong answer this project has to keep not taking."""
    assert "argmax(z / T) == argmax(z)" in _section_source("regime_audit")
