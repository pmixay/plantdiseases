"""
Defensive invariants for the training pipeline (audit followups).

These tests don't execute the notebook — they validate the parts of the
pipeline we can statically check (label maps, keyword lists, helper
existence) and the data-on-disk invariants we can verify when an actual
training run has produced output. Notebook execution itself is gated on
GPU availability and would be too slow for CI.

Run with:
    pytest server/tests/test_data_invariants.py -q

The notebook's data-on-disk tests will skip cleanly if `data_v2/` doesn't
exist yet (i.e., before §2 has been run for the first time).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NB_PATH = REPO_ROOT / "server" / "train_notebook.ipynb"
DATA_ROOT = REPO_ROOT / "data_v2"


def _notebook_source() -> str:
    """Concatenate every code cell in the training notebook into a single
    string. Easier to grep than walking nbformat cells per-test."""
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    return "\n\n".join(
        "".join(c.get("source", []))
        for c in nb["cells"]
        if c["cell_type"] == "code"
    )


# ── Static invariants on the notebook source ────────────────────────


def test_pd_label_map_drops_rust_and_fruit_diseases():
    """audit fix #3 + #11: rust morphology and fruit-stage diseases must
    NOT map to leaf_spot_fungal."""
    src = _notebook_source()
    # The drop list must mention rust + fruit rot keywords.
    assert "_PD_DROP_KEYWORDS" in src, (
        "PlantDoc drop list missing — audit fix #3 keyword guard"
    )
    for must_drop in ("'rust'", "'scab'", "'black rot'"):
        assert must_drop in src, f"PD drop list missing {must_drop}"


def test_pv_label_map_excludes_physiological_and_fruit():
    """audit fix #11: Strawberry leaf scorch (physiological) and apple
    black rot / cedar apple rust (fruit / rust) must NOT land in
    leaf_spot_fungal."""
    src = _notebook_source()
    bad_pairs = [
        ("'Strawberry___Leaf_scorch'", "'leaf_spot_fungal'"),
        ("'Apple___Black_rot'", "'leaf_spot_fungal'"),
        ("'Apple___Cedar_apple_rust'", "'leaf_spot_fungal'"),
        ("'Corn_(maize)___Common_rust_'", "'leaf_spot_fungal'"),
    ]
    # Find every line containing the LHS and check it doesn't map to
    # the RHS.
    for lhs, rhs in bad_pairs:
        for line in src.splitlines():
            if lhs in line and ":" in line:
                # Allow None mapping; refuse the leaf_spot_fungal mapping.
                assert rhs not in line, (
                    f"PV label {lhs} still maps to {rhs} — audit fix #11"
                )


def test_plantdoc_loop_shuffles_row_order():
    """audit fix #10: PlantDoc loop must shuffle row order with a seeded
    RNG instead of iterating ds[split] in HF row order."""
    src = _notebook_source()
    plantdoc_section = re.search(
        r"def fetch_plantdoc.*?(?=\ndef\s|\nclass\s)", src, re.DOTALL,
    )
    assert plantdoc_section, "fetch_plantdoc body not found"
    body = plantdoc_section.group(0)
    # Must seed a Random off SEED and shuffle a list called `order`.
    # Don't try to be paren-aware in the regex — the shuffle is on its
    # own line and the seed expression may contain a conditional.
    assert "random.Random(SEED" in body and ".shuffle(order)" in body, (
        "fetch_plantdoc is not seeded-shuffling its row order — audit fix #10"
    )


def test_coco_exclude_list_covers_food_and_dining():
    """audit fix #4: the COCO not_a_plant filter must exclude food /
    dining categories that frequently co-occur with plants."""
    src = _notebook_source()
    must_exclude = ("'sandwich'", "'cake'", "'donut'", "'pizza'", "'dining table'")
    for cat in must_exclude:
        assert cat in src, (
            f"COCO exclude list missing {cat} — audit fix #4"
        )


def test_botrytis_green_content_filter_present():
    """audit fix #5: iNat Botrytis must run a post-fetch green-content
    filter to drop agar plates / fruit / dead-tissue observations."""
    src = _notebook_source()
    assert "_drop_low_green_content" in src, "audit fix #5 helper missing"
    # And it must be applied to gray_mold_botrytis.
    assert re.search(
        r"_drop_low_green_content\([^)]*gray_mold_botrytis[^)]*\)",
        src,
    ), "audit fix #5 — helper exists but isn't called on Botrytis"


def test_pv_healthy_cap_lowered():
    """audit fix #6: PV `healthy` must use a per-class cap override that
    keeps PV from dominating the healthy class."""
    src = _notebook_source()
    assert "PV_PER_CLASS_TRAIN_OVERRIDE" in src
    m = re.search(
        r"PV_PER_CLASS_TRAIN_OVERRIDE\s*=\s*\{[^}]*'healthy'\s*:\s*(\d+)",
        src,
    )
    assert m, "PV per-class cap missing"
    cap = int(m.group(1))
    # 250 was the default chosen in the patch; allow tightening but not loosening
    # back toward the studio-dominated 900.
    assert cap <= 350, (
        f"PV healthy cap is {cap} — too high; lab-uniform-bg dominates "
        f"the class (audit fix #6)"
    )


def test_pre_split_dedup_runs_before_splitters():
    """audit fix #1: dedup must run on TRAIN_ROOT before make_val_*."""
    src = _notebook_source()
    # _dedup_train_in_place must exist and be called before make_val_realworld.
    assert "_dedup_train_in_place" in src
    dedup_pos = src.find("_dedup_train_in_place(TRAIN_ROOT")
    split_pos = src.find("make_val_realworld()")
    assert 0 < dedup_pos < split_pos, (
        "Dedup is not running BEFORE the splitters — audit fix #1"
    )


def test_phash_cross_check_present():
    """audit fix #1: pHash leakage check between TRAIN and held-out splits.

    The check evolved from a hard-fail audit (``assert_…``) to a
    self-resolving step (``resolve_…``) that drops the held-out side of
    each colliding pair — PlantDoc / PlantVillage publish near-duplicate
    photos across their train/test boundary, and raising on every run
    blocked legitimate training. Either name satisfies the contract.
    """
    src = _notebook_source()
    assert re.search(
        r"\b(?:assert_no|resolve)_phash_split_leakage\b", src
    ), "audit fix #1 — pHash leakage check missing"
    # And it must be invoked at the end of §3.
    assert re.search(
        r"(?:assert_no|resolve)_phash_split_leakage\(TRAIN_ROOT\s*,\s*SPLIT_ROOTS",
        src,
    ), "audit fix #1 — pHash leakage check defined but not called"


def test_head_phase_uses_flat_lr_with_early_stop():
    """audit fix #7: phase-1 teacher must NOT cosine to ~0 in epoch 3.
    Use cosine=False + early_stop=True."""
    src = _notebook_source()
    # Look at the head-phase invocation specifically.
    head_call = re.search(
        r"_train_phase\(teacher,\s*opt,\s*3,\s*_make_sched\(opt,\s*3,\s*len\(train_dl\)[^)]*\)\s*,\s*'head'[^)]*\)",
        src, re.DOTALL,
    )
    assert head_call, "head-phase call site missing"
    block = head_call.group(0)
    assert "cosine=False" in block, "audit fix #7 — head phase must use cosine=False"
    assert "early_stop=True" in block, "audit fix #7 — head phase must early-stop+restore"


def test_ema_floats_bn_buffers_with_decay():
    """audit fix #8: EMA must apply exponential moving average to
    floating-point BN buffers (running_mean / running_var), not blindly
    copy them from the online model."""
    src = _notebook_source()
    # Look at the body of ema_update.
    m = re.search(r"def ema_update\(.*?\n(?:.*?\n){1,40}", src)
    assert m, "ema_update body not found"
    body = m.group(0)
    assert "is_floating_point" in body, (
        "audit fix #8 — ema_update doesn't differentiate "
        "floating buffers from int counters"
    )
    # And the float branch must use mul_/add_ pattern (proper EMA).
    assert re.search(
        r"b_ema\.mul_\(decay\)\.add_\(b_on(?:\.detach\(\))?,\s*alpha=1\.0?\s*-\s*decay\)",
        body,
    ), "audit fix #8 — float buffer EMA formula missing"


def test_kd_keeps_topk_candidates():
    """audit fix #9: KD loop must persist top-K (≥2) EMA snapshots so
    §6 can pick the winner at INT8."""
    src = _notebook_source()
    assert "TOPK" in src and "candidate_results" in src
    assert "classifier_candidates.pt" in src, (
        "audit fix #9 — KD candidate file missing"
    )


def test_int8_select_by_deployed_metric():
    """§6 must evaluate every top-K candidate at INT8 and pick the
    winner from the resulting metrics — not blindly export the FP32-
    best candidate. The selection target was tightened: an earlier
    version sorted by ``int8_realworld_f1``, which leaked the ship-
    gate slice into the selector (val_realworld was both selector
    and judge, a low-DoF but real source of optimism bias). The
    winner is now picked by ``int8_indomain_f1`` so val_realworld
    stays a clean ship gate; both indomain and realworld INT8 numbers
    are still measured so the drift gate / NAP MaxLogit fit / ship
    gate downstream have what they need.
    """
    src = _notebook_source()
    assert "_export_and_eval_int8_candidate" in src, (
        "INT8 candidate evaluator missing"
    )
    assert re.search(
        r"candidate_results\.sort\(key=lambda r: -r\['int8_indomain_f1'\]\)",
        src,
    ), "winner selection must sort by int8_indomain_f1 (val_realworld is the ship gate, not the selector)"
    assert "int8_realworld_f1" in src, (
        "realworld INT8 metric must still be computed — the drift gate, "
        "NAP MaxLogit fit and ship gate downstream read it"
    )


def test_make_group_split_skips_empty_class_subdirs():
    """audit follow-up: temp_cal / conformal_cal restrict
    allowed_sources to {plantdoc, inat}, so `not_a_plant` (COCO-only)
    has zero groups for those splits. The splitter must NOT create
    an empty class subdirectory — torchvision.ImageFolder rejects
    empty class dirs with FileNotFoundError on construction.
    """
    src = _notebook_source()
    # Find the make_group_split body (it's the function with the
    # ratio + seed + allowed_sources signature).
    m = re.search(
        r"def make_group_split\(.*?\n(?:.*?\n){10,80}",
        src, re.DOTALL,
    )
    assert m, "make_group_split body not found"
    body = m.group(0)
    # The split must guard before calling .mkdir on dest_root/cls.
    assert "if k <= 0:" in body, (
        "make_group_split is creating empty class subdirs — will trip "
        "ImageFolder on temp_cal/conformal_cal (audit follow-up)"
    )


def test_make_eval_loader_uses_allow_empty_when_supported():
    """audit follow-up: belt-and-braces against empty class subdirs.
    `make_eval_loader` should pass `allow_empty=True` to ImageFolder
    when the installed torchvision supports it (≥ 0.18)."""
    src = _notebook_source()
    assert "_IMAGEFOLDER_ALLOW_EMPTY" in src
    assert re.search(
        r"if_kwargs\s*=\s*\{'allow_empty':\s*True\}",
        src,
    ), "make_eval_loader is not threading allow_empty into ImageFolder"


def test_ood_floor_is_calibrated_not_hardcoded():
    """audit fix #2: classes.json max_logit_floor must be computed from
    the MaxLogit distribution on plants vs NAP, not hardcoded to 4.0."""
    src = _notebook_source()
    # Calibration block must exist
    assert "TARGET_PLANT_FPR" in src, "audit fix #2 — calibration target missing"
    assert "_maxlogit_distribution" in src, "audit fix #2 — distribution helper missing"
    # And classes_payload must read ML_FLOOR (not the literal 4.0)
    m = re.search(
        r"'max_logit_floor'\s*:\s*([A-Z_a-z0-9.]+)",
        src,
    )
    assert m, "max_logit_floor entry missing"
    val = m.group(1)
    assert val in ("ML_FLOOR", "ML_FLOOR_PERSISTED"), (
        f"max_logit_floor is {val}, must reference the calibrated "
        f"ML_FLOOR / ML_FLOOR_PERSISTED variable, not a hardcoded number"
    )


# ── Data-on-disk invariants (skipped if `data_v2/` isn't present) ───


def _data_v2_or_skip():
    if not DATA_ROOT.exists():
        pytest.skip(
            "data_v2/ does not exist yet — run notebook §2 first to "
            "populate train + held-out splits"
        )


def _iter_images(root: Path):
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            yield p


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


SPLIT_DIRS = (
    "classification/val/realworld",
    "classification/val/indomain",
    "classification/val/temperature_cal",
    "classification/val/conformal_cal",
    "classification/val/plantvillage_studio",
)


def test_no_sha256_leakage_between_train_and_holdout():
    """Belt-and-braces: §3 already asserts this in the notebook, but if
    someone runs only §2 and stops, the on-disk state is checkable from
    CI without GPU. Skip cleanly if no data on disk."""
    _data_v2_or_skip()
    train_root = DATA_ROOT / "classification" / "train"
    train_hashes = {_sha256(p): p for p in _iter_images(train_root)}
    if not train_hashes:
        pytest.skip("TRAIN_ROOT empty — nothing to audit")
    overlaps = []
    for split_subdir in SPLIT_DIRS:
        for p in _iter_images(DATA_ROOT / split_subdir):
            h = _sha256(p)
            if h in train_hashes:
                overlaps.append((str(train_hashes[h]), str(p)))
                if len(overlaps) >= 5:
                    break
        if len(overlaps) >= 5:
            break
    assert not overlaps, (
        "Train/heldout SHA-256 overlap detected — pre-split dedup "
        "(audit fix #1) failed:\n  "
        + "\n  ".join(f"train={a}  heldout={b}" for a, b in overlaps)
    )


def test_no_class_silently_empty_after_split():
    """If §2 ran successfully, every class should have at least 5 samples
    in val_realworld (the THRESH_VAL guard inside the notebook). This
    re-asserts the same invariant at CI time so a partial state can't
    sneak in."""
    _data_v2_or_skip()
    val_realworld = DATA_ROOT / "classification" / "val" / "realworld"
    if not val_realworld.exists():
        pytest.skip("val_realworld not built yet")
    classes_json = REPO_ROOT / "server" / "models" / "classes.json"
    if not classes_json.exists():
        pytest.skip("classes.json not produced yet (post-train artefact)")
    cls_names = json.loads(classes_json.read_text()).get("class_names") or []
    underflow = []
    for cls in cls_names:
        n = sum(1 for _ in _iter_images(val_realworld / cls))
        if n < 5:
            underflow.append((cls, n))
    assert not underflow, (
        f"val_realworld has classes with < 5 samples: {underflow}. "
        "PlantDoc may have failed to download — see notebook §2 retry path."
    )


def test_int8_drift_within_fail_threshold():
    """Once `model_card.json` exists, refuse to ship if INT8 drift > 0.05.
    The notebook itself raises during §6, but this catches the case where
    someone produced the artefact via a custom path.

    Exception: when static-INT8 (QDQ + QOperator retry) exceeds the
    fail-drift threshold, §6 ships the FP32 graph honestly under
    ``classifier_fp32.onnx`` (NOT renamed as ``classifier_int8.onnx``)
    and records ``shipped=False`` with
    ``shipped_artifact="classifier_fp32.onnx (int8 export failed drift gate)"``.
    The drift in that record is from the rejected quant attempt, not the
    actually-shipped artefact, so this gate does not apply — but we still
    assert that ``shipped_artifact`` reflects the documented fallback.
    """
    mc = REPO_ROOT / "server" / "models" / "model_card.json"
    if not mc.exists():
        pytest.skip("model_card.json not produced yet (post-train artefact)")
    data = json.loads(mc.read_text())
    int8 = data.get("int8") or {}
    drift = int8.get("drift")
    if drift is None:
        pytest.skip("int8.drift not in model_card.json")
    if int8.get("shipped") is False:
        # The §6 honest FP32 fallback path.
        shipped = int8.get("shipped_artifact") or ""
        accepted = ("fp32", "int8 export failed", "renamed")
        assert any(s in shipped.lower() for s in accepted), (
            f"int8.shipped=False but shipped_artifact={shipped!r} — "
            f"expected the documented 'classifier_fp32.onnx (int8 export "
            f"failed drift gate)' fallback (or the legacy "
            f"'classifier.onnx (renamed)' marker on pre-v2.2 builds)"
        )
        pytest.skip(
            f"INT8 quant fell back to FP32 (drift {drift:+.4f} from the "
            f"rejected quant attempt). Gate does not apply."
        )
    assert float(drift) <= 0.05, (
        f"INT8 drift {drift:+.4f} exceeds the 0.05 fail threshold — "
        f"refuse to ship"
    )
