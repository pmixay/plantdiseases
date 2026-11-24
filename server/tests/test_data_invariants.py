"""
Defensive invariants for the training pipeline (audit followups).

These tests don't execute the notebook — they validate the parts of the
pipeline we can statically check (label maps, keyword lists, helper
existence) by grepping the notebook source. Notebook execution itself is
gated on GPU availability and would be too slow for CI, and nothing here
looks at data on disk.

Run with:
    pytest server/tests/test_data_invariants.py -q
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NB_PATH = REPO_ROOT / "server" / "train_notebook.ipynb"


@functools.lru_cache(maxsize=1)
def _notebook_source() -> str:
    """Concatenate every code cell in the training notebook into a single
    string. Easier to grep than walking nbformat cells per-test. Cached: the
    notebook is over half a megabyte of JSON and nearly every test here reads
    it."""
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    return "\n\n".join(
        "".join(c.get("source", []))
        for c in nb["cells"]
        if c["cell_type"] == "code"
    )


def _source_weights_block() -> str:
    """Just the ``SOURCE_WEIGHTS = { … }`` literal from §4.

    Sampler-weight assertions must read THIS block, not the whole notebook: a
    bare ``'<source>': <number>`` grep matches any other dict keyed by source
    name (§2's ``REQUIRED_SOURCE_FLOORS``, ``PRIOR_SOURCE_WEIGHTS``, a per-source
    cap table…) and silently asserts against the wrong number — whichever
    happens to appear first in cell order.
    """
    src = _notebook_source()
    # Anchored at line start so a future ``PRIOR_SOURCE_WEIGHTS = {`` literal
    # (today it's ``dict(SOURCE_WEIGHTS)``) can't be picked up by the substring.
    m = re.search(r"^SOURCE_WEIGHTS = \{", src, re.MULTILINE)
    assert m, "SOURCE_WEIGHTS dict literal not found in the notebook"
    end = src.index("\n}", m.start())
    return src[m.start():end + 2]


# ── Static invariants on the notebook source ────────────────────────


def test_pd_label_map_drops_rust_and_fruit_diseases():
    """audit fix #3 + #11: rust morphology and fruit-stage diseases must
    NOT map to leaf_spot."""
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
    leaf_spot."""
    src = _notebook_source()
    bad_pairs = [
        ("'Strawberry___Leaf_scorch'", "'leaf_spot'"),
        ("'Apple___Black_rot'", "'leaf_spot'"),
        ("'Apple___Cedar_apple_rust'", "'leaf_spot'"),
        ("'Corn_(maize)___Common_rust_'", "'leaf_spot'"),
    ]
    # Find every line containing the LHS and check it doesn't map to
    # the RHS.
    for lhs, rhs in bad_pairs:
        for line in src.splitlines():
            if lhs in line and ":" in line:
                # Allow None mapping; refuse the leaf_spot mapping.
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

    From 2.7.2 the target is ``_MUTABLE_SPLITS`` rather than ``SPLIT_ROOTS``:
    the permanent holdout is excluded because dropping rows from it would make
    its membership a function of the training corpus, which is the one property
    its stable hash bucket exists to guarantee. Leakage against the holdout is
    still resolved — on the TRAIN side instead — and that reversed pass is
    asserted below, so this cannot be relaxed into "the holdout is simply not
    checked".
    """
    src = _notebook_source()
    assert re.search(
        r"\b(?:assert_no|resolve)_phash_split_leakage\b", src
    ), "audit fix #1 — pHash leakage check missing"
    # And it must be invoked at the end of §3, over the mutable splits.
    assert re.search(
        r"(?:assert_no|resolve)_phash_split_leakage\(TRAIN_ROOT\s*,\s*"
        r"(?:SPLIT_ROOTS|_MUTABLE_SPLITS)",
        src,
    ), "audit fix #1 — pHash leakage check defined but not called"


def test_holdout_leakage_is_resolved_on_the_train_side():
    """The holdout is a fixed instrument: nothing may delete from it.

    ``audit_cross_class_phash_leakage(..., drop=True)`` unlinks rows from the
    roots it is given. Pointed at the holdout it would remove a different subset
    every release (the set depends on what happens to be in TRAIN_ROOT), so two
    releases would score on different images and the delta between them would be
    meaningless. The leak still has to be resolved, so the arguments are
    reversed: HOLDOUT becomes the reference and TRAIN_ROOT is what gets pruned.
    """
    src = _notebook_source()
    assert "_MUTABLE_SPLITS = tuple(r for r in SPLIT_ROOTS if r != HOLDOUT)" in src, (
        "the mutating §3 guards are no longer excluding HOLDOUT"
    )
    assert re.search(
        r"audit_cross_class_phash_leakage\(\s*HOLDOUT\s*,\s*\(\s*TRAIN_ROOT\s*,?\s*\)",
        src,
    ), "holdout-vs-train leakage is never resolved — the instrument can be leaked into"
    # The mutating pass must NOT be handed the holdout.
    assert not re.search(
        r"audit_cross_class_phash_leakage\(TRAIN_ROOT\s*,\s*SPLIT_ROOTS", src
    ), "the dropping audit is still pointed at SPLIT_ROOTS, which includes HOLDOUT"


def _augmentation_helpers():
    """Execute only the pure-Pillow augmentation helpers, never notebook work."""
    import math
    import random

    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFilter

    src = _notebook_source()
    start = src.index("class LocalSpecularGlare:")
    end = src.index("class JpegRecompress:", start)
    namespace = {
        "math": math, "random": random, "PILImage": PILImage,
        "ImageDraw": ImageDraw, "ImageFilter": ImageFilter,
    }
    exec(src[start:end], namespace)  # noqa: S102 - audited isolated definitions
    return namespace


def test_augmentation_geometry_matches_serving():
    """Training sees the serving transform (shorter-side Resize at
    EVAL_RESIZE_RATIO, then an IMG crop) with only the crop window randomised.

    The first 2.8.0 draft letterboxed the full frame at train time while every
    eval slice, both calibration splits, the server and the Aqua client
    centre-crop — a ~1.4x scale shift on a 4:3 frame plus padding bars serving
    never produces. No letterbox, RRC or erasing may return on the train side;
    a full-frame policy has to change the SERVING transform and re-calibrate.
    """
    src = _notebook_source()
    assert "'version': 'serve_parity_v2_unablated'" in src
    assert "T.Resize(int(IMG * EVAL_RESIZE_RATIO)), T.RandomCrop(IMG)" in src
    # The eval/calibration loader and the crop eval use the same constant.
    assert "T.Resize(int(img_size * EVAL_RESIZE_RATIO))" in src
    assert "T.Resize(int(IMG * EVAL_RESIZE_RATIO)), T.CenterCrop(IMG)" in src
    assert "class PadToSquare" not in src and "PadToSquare()" not in src
    assert "RandomResizedCrop(" not in src
    assert "RandomErasing(" not in src
    assert "_geo_aug = indoor_train_pil_tx" in src, (
        "Aqua and Gamma must not train on different augmentation distributions"
    )


def test_augmentation_off_switch_defaults_on_and_relabels_the_ablation_arm():
    """``TRAIN_AUGMENTATION`` defaults to ``'on'`` (the unablated profile,
    unchanged) and is validated. ``'off'`` drops only the photometric pieces
    (sensor artifacts + ColorJitter), keeps the serving-parity geometry, and
    seals a DIFFERENT profile version so an ablation run can never be filed
    under the default recipe in run_manifest.json / model_card.json.
    """
    src = _notebook_source()
    assert "TRAIN_AUGMENTATION = 'on'\n" in src
    assert "TRAIN_AUGMENTATION_MODES = ('on', 'off')" in src
    assert "if TRAIN_AUGMENTATION not in TRAIN_AUGMENTATION_MODES:\n    raise ValueError(" in src
    default_label = "'version': 'serve_parity_v2_unablated'"
    off_label = "'version': 'serve_parity_v2_geometry_only'"
    assert default_label in src and off_label in src and off_label != default_label
    off_branch = src[src.index("if TRAIN_AUGMENTATION == 'off':"):]
    off_branch = off_branch[: off_branch.index("_photometric_tx = []")]
    assert off_label in off_branch
    assert "'sensor_artifact': 'none'" in off_branch
    assert "'sensor_artifact_p': 0, 'color_jitter_p': 0" in off_branch
    assert "'efficacy_status':" in off_branch
    # The Compose is built from the profile; zero-probability stages are
    # omitted, never wrapped at p=0, and geometry stays unconditional.
    assert "if TRAIN_AUGMENTATION_PROFILE['sensor_artifact_p'] > 0:" in src
    assert "if TRAIN_AUGMENTATION_PROFILE['color_jitter_p'] > 0:" in src
    assert (
        "T.Resize(int(IMG * EVAL_RESIZE_RATIO)), T.RandomCrop(IMG), T.RandomHorizontalFlip(),\n"
        "    *_photometric_tx,\n"
    ) in src
    # The label reaches every place the profile is recorded.
    assert "log_run({'phase': 'augmentation_profile', **TRAIN_AUGMENTATION_PROFILE})" in src
    assert src.count("'augmentation_profile': TRAIN_AUGMENTATION_PROFILE,") == 2


def _notebook_markdown() -> str:
    nb = json.loads(NB_PATH.read_text(encoding="utf-8"))
    return "\n\n".join(
        "".join(c.get("source", []))
        for c in nb["cells"]
        if c["cell_type"] == "markdown"
    )

def test_motion_blur_is_directional_and_glare_is_local():
    """Guard against the old BoxBlur/global-brightness implementations."""
    import random

    import numpy as np
    from PIL import Image as PILImage

    helpers = _augmentation_helpers()
    horizontal = helpers["_directional_motion_kernel"](angle_degrees=0)
    vertical = helpers["_directional_motion_kernel"](angle_degrees=90)
    assert horizontal != vertical
    assert sum(horizontal) == pytest.approx(5.0)
    assert {i // 5 for i, value in enumerate(horizontal) if value} == {2}
    assert {i % 5 for i, value in enumerate(vertical) if value} == {2}

    random.seed(7)
    glare = helpers["LocalSpecularGlare"](p=1.0)
    before = PILImage.new("RGB", (100, 100), "black")
    after = np.asarray(glare(before))
    changed_fraction = float((after.max(axis=2) > 0).mean())
    assert 0.0 < changed_fraction < 0.65, (
        "glare should be a local soft highlight, not global brightness"
    )


def test_head_training_early_stops_and_restores_best():
    """Report Stage 1: the production model is a frozen DINOv2 + trained
    head. The head loop must early-stop on a plateau and restore the best
    checkpoint (so a late-epoch overfit can't ship)."""
    src = _notebook_source()
    assert "HEAD_PATIENCE" in src, "head-training patience knob missing"
    assert "plateau >= HEAD_PATIENCE" in src, (
        "head training does not early-stop on a plateau"
    )
    # The best checkpoint must be captured and restored before export.
    assert "best_state" in src and "model.load_state_dict(best_state)" in src, (
        "head training does not restore the best checkpoint before §6 export"
    )


def test_backbone_base_frozen_head_and_optional_lora_trained():
    """Report Stage 1: the DINOv2 ViT-S/14 BASE weights must stay FROZEN
    (requires_grad=False) and the backbone never leaves eval(), so the
    LayerNorm stats are identical at train / eval / export time. The head
    always trains; the optional LoRA adapters (later blocks) may also train
    at a lower LR. The old EMA-teacher machinery is gone."""
    src = _notebook_source()
    assert "DINOv2Classifier" in src, "frozen-DINOv2 model class missing"
    assert "p.requires_grad_(False)" in src, "backbone base params are not frozen"
    assert "def _set_backbone_train_mode" in src and "backbone.eval()" in src, (
        "backbone base must stay in eval() during head/LoRA training"
    )
    assert "'lora_dropout' in name" in src and "module.train()" in src, (
        "PEFT LoRA dropout must be re-enabled after the frozen base enters eval mode"
    )
    assert "torch.optim.AdamW(model.head.parameters()" in src, (
        "the head must always be optimised"
    )
    # The removed EMA-teacher machinery must not creep back.
    assert "ema_update" not in src, "EMA-teacher machinery should be gone (no KD)"


def test_lora_is_low_rank_later_blocks_and_merged_for_export():
    """Report 'last few points': LoRA must (a) be toggleable, (b) adapt only
    the LATER blocks (early frozen), (c) feed its params to the optimiser at
    a lower LR than the head, and (d) be merged into the base Linears BEFORE
    the ONNX export so the shipped graph stays a single self-contained file
    (validated parity ~1e-6). The base stays frozen regardless."""
    src = _notebook_source()
    assert re.search(r"USE_LORA\s*=\s*(True|False)", src), "no USE_LORA toggle"
    assert "get_peft_model(" in src and "LoraConfig(" in src, "peft LoRA not wired"
    assert "LORA_LAST_K_BLOCKS" in src, "LoRA must restrict to the later blocks"
    # lower LR for the adapters than the head
    assert re.search(r"'params':\s*_lora_params,\s*'lr':\s*LORA_LR", src), (
        "LoRA params must be a distinct optimiser group at LORA_LR"
    )
    # merged before export, in the USE_LORA branch, so the ONNX is one graph
    assert "merge_and_unload()" in src, "LoRA must be merged before ONNX export"
    mpos = src.find("merge_and_unload()")
    epos = src.find("export_model = model.to('cpu').eval()")
    assert 0 < mpos < epos, "merge_and_unload must run BEFORE the ONNX export"


def test_hflip_tta_enabled_with_logit_averaging():
    """Report Stage 3: hflip TTA is near-free given the CPU headroom and is
    now ON. It must average LOGITS (so temperature/conformal/MaxLogit stay
    valid) and the same mode is written to classes.json for the server."""
    src = _notebook_source()
    assert re.search(r"USE_TTA_HFLIP\s*=\s*True", src), "hflip TTA should be enabled"
    assert "x_np[..., ::-1]" in src, "TTA must flip along the width axis"


def test_stage0_weakclass_supply_productive():
    """Weak-class free-text breadth. This originally required >=18 terms per
    physiological class as a proxy for "enough supply". Live source verification
    (2026-06, see server/DATA_SOURCES_VERIFICATION.md) showed many of those
    terms returned ZERO research-grade+photo observations — fake breadth that
    only spent API calls — so the dead queries were pruned. The real guards for
    weak-class supply are now (a) a non-gutted list of PRODUCTIVE terms, (b) the
    per-class count floor (raises if a class is starved), and (c) DoctorP, the
    eval-eligible real-photo source that actually seeds the weak-class eval
    slices. Assert the productive lists stay reasonably broad + the target is
    raised so they're fully drawn."""
    src = _notebook_source()
    block = re.search(r"INAT_PHYSIOLOGICAL_QUERIES\s*=\s*\{.*?\n\}", src, re.DOTALL)
    assert block, "physiological query table missing"
    for cls in ("leaf_scorch_underwatering", "nutrient_deficiency"):
        seg = re.search(rf"'{cls}':\s*\[(.*?)\]", block.group(0), re.DOTALL)
        assert seg, f"{cls} query list missing"
        # Count quoted strings on the CODE portion of each line only — a comment
        # may quote a pruned term for documentation, which must not be counted.
        terms = sum(
            len(re.findall(r"'[^']+'", line.split("#", 1)[0]))
            for line in seg.group(1).splitlines()
        )
        assert terms >= 10, (
            f"{cls} productive query list too thin ({terms} terms) — weak-class "
            f"supply breadth must not be gutted"
        )
    assert "fetch_inat_physiological, per_class=1500" in src, (
        "per_class target must be raised so the query set is fully drawn"
    )


def test_knowledge_distillation_scoped_to_aqua_only():
    """Design invariant: the SERVER-served gamma/frontier model is the frozen
    DINOv2 + MLP head — no distillation in that path. KD was deliberately
    re-introduced ONLY to produce the **Aqua** on-device student
    (MobileNetV3-Small, §5), gated behind combined mode's distill flag so it never touches
    the gamma/frontier pipeline. The earlier EfficientNetV2-S→MobileNetV3
    multi-candidate teacher-snapshot machinery (the genuinely wrong tool) must
    stay gone."""
    src = _notebook_source()
    # Legacy multi-candidate teacher/snapshot machinery must stay removed.
    for gone in ("ema_update", "_train_phase(", "classifier_candidates",
                 "candidate_results"):
        assert gone not in src, (
            f"legacy distillation machinery {gone!r} is back — only the scoped "
            f"Aqua MobileNetV3 student KD should exist"
        )
    # KD is gated behind combined mode's distill flag, not run unconditionally.
    assert "FAMILY_CONFIG.get('distill')" in src, (
        "Aqua KD must be gated behind combined mode's distill flag"
    )
    # The Aqua student is a MobileNetV3-Small distilled from the gamma teacher.
    assert "mobilenet_v3_small" in src, (
        "the Aqua student should be a MobileNetV3-Small distilled from gamma"
    )
    # The gamma/frontier production model stays the frozen DINOv2 + head.
    assert "DINOv2Classifier" in src, (
        "gamma/frontier production model must remain the frozen DINOv2 + head"
    )


def test_int8_is_dynamic_with_drift_gate_and_paired_bootstrap():
    """Report Stage 1 + Stage 0: a ViT must be quantised with DYNAMIC INT8
    (ORT guidance: dynamic for transformers, static for CNNs), gated on a
    measured FP32-vs-INT8 drift, and the int8-vs-fp32 ordering reported
    with a PAIRED BOOTSTRAP CI (the "0.73 > 0.69 is noise" gate — a 0.04
    gap at n≈450 is within sampling error)."""
    src = _notebook_source()
    assert "quantize_dynamic" in src and "QuantType.QInt8" in src, (
        "ViT must use dynamic INT8 quantisation (ORT guidance for transformers)"
    )
    assert "FAIL_DRIFT" in src and re.search(r"drift\s*>\s*FAIL_DRIFT", src), (
        "INT8 must be gated on a measured FP32-vs-INT8 drift with a fail threshold"
    )
    assert "_paired_bootstrap_f1" in src and "'paired_bootstrap'" in src, (
        "the int8-vs-fp32 ordering must be reported with a paired-bootstrap CI "
        "(report Stage 0 — point F1 alone is noise at this sample size)"
    )


def test_crop_nutrient_supplement_train_only():
    """The weak nutrient_deficiency class is supplemented with a curated
    crop-domain abiotic-stress dataset. CoLeaf (Mendeley) is no longer fetched
    — Mendeley/Cloudflare 403s datacenter IPs (incl. Colab), so it always
    failed; MPLD (Zenodo) is the surviving crop supplement. The supplement must
    (a) be fetched into nutrient_deficiency before the pre-split dedup, (b)
    carry a HIGH label_confidence (curated, unlike the noisy iNat free-text),
    and (c) be TRAIN-ONLY — neither 'mpld' nor 'coleaf' may appear in any
    make_*_split allowed_sources, or crop imagery would leak into the in-domain
    houseplant eval/cal splits and corrupt the metrics."""
    src = _notebook_source()
    # CoLeaf was removed entirely (Mendeley 403s datacenter IPs incl. Colab, so
    # it never downloaded). This guards against it being re-added.
    assert "_try_fetch('coleaf_nutrient_deficiency'" not in src, (
        "CoLeaf must NOT be fetched — it 403s from Colab; mirror it to Kaggle to "
        "restore"
    )
    # MPLD is the active crop-nutrient supplement, fetched before dedup.
    assert "fetch_mpld_phosphorus_deficiency" in src, "MPLD supplement missing"
    m = re.search(r"def fetch_mpld_phosphorus_deficiency\([^)]*label_confidence=([0-9.]+)", src)
    assert m and float(m.group(1)) >= 0.85, (
        "MPLD should ship a high label_confidence (curated labels)"
    )
    mpld_pos = src.find("_try_fetch('mpld_phosphorus_deficiency'")
    dedup_pos = src.find("_dedup_train_in_place(TRAIN_ROOT")
    assert 0 < mpld_pos < dedup_pos, "MPLD fetch must run before the pre-split dedup"
    # Train-only: neither crop source may be an allowed source on any eval split.
    for split_fn in ("make_val_realworld", "make_val_indomain",
                     "make_temperature_cal", "make_conformal_cal"):
        body = re.search(rf"def {split_fn}\(.*?\n(?:.*\n){{0,12}}", src)
        assert body and "coleaf" not in body.group(0) and "mpld" not in body.group(0), (
            f"{split_fn} must NOT allow crop sources (coleaf/mpld) — they would "
            f"corrupt the in-domain houseplant eval slice"
        )


def test_coleaf_removed_entirely():
    """CoLeaf (Mendeley) was removed: it 403s datacenter IPs (incl. Colab) so it
    never downloaded. No fetcher, no zip table, no sampler/prior weight may
    remain — otherwise the dead code/config invites a re-add that 403-spams the
    run. MPLD (Zenodo) is the surviving crop-nutrient supplement."""
    src = _notebook_source()
    assert "def fetch_coleaf" not in src, "the CoLeaf fetcher must be gone"
    assert "COLEAF_DEFICIENCY_ZIPS" not in src, "the CoLeaf zip table must be gone"
    assert "_try_fetch('coleaf" not in src, "CoLeaf must never be fetched"
    assert re.search(r"'coleaf'\s*:", src) is None, "no 'coleaf' SOURCE_WEIGHTS entry"
    assert "PRIOR_SOURCE_WEIGHTS['coleaf']" not in src, "no 'coleaf' prior weight"


def test_mpld_supplements_nutrient_deficiency_train_only():
    """Report Stage 2 (species diversity): a SECOND nutrient-deficiency
    source so the signal isn't tied to coffee morphology. MPLD (maize
    phosphorus, Zenodo 10041514, CC BY 4.0) must (a) be fetched into
    nutrient_deficiency, (b) carry a HIGH label_confidence (expert-
    controlled, unlike the noisy iNat free-text), and (c) be TRAIN-ONLY —
    its 'mpld' source must NOT appear in any make_*_split allowed_sources,
    or crop imagery would leak into the in-domain houseplant eval/cal
    splits and corrupt the metrics."""
    src = _notebook_source()
    assert "fetch_mpld_phosphorus_deficiency" in src, "MPLD supplement missing"
    assert "_MPLD_URL" in src and "10041514" in src, (
        "MPLD Zenodo source (record 10041514) not wired in"
    )
    m = re.search(r"def fetch_mpld_phosphorus_deficiency\([^)]*label_confidence=([0-9.]+)", src)
    assert m and float(m.group(1)) >= 0.85, (
        "MPLD should ship a high label_confidence (curated expert labels)"
    )
    # Train-only: 'mpld' must never be an allowed source on any eval split.
    for split_fn in ("make_val_realworld", "make_val_indomain",
                     "make_temperature_cal", "make_conformal_cal"):
        body = re.search(rf"def {split_fn}\(.*?\n(?:.*\n){{0,12}}", src)
        assert body and "mpld" not in body.group(0), (
            f"{split_fn} must NOT allow the 'mpld' source — MPLD is a crop "
            f"supplement and would corrupt the in-domain houseplant eval slice"
        )
    # And it must run before the dedup/split so its rows are deduped + scoped.
    mpld_pos = src.find("_try_fetch('mpld_phosphorus_deficiency'")
    dedup_pos = src.find("_dedup_train_in_place(TRAIN_ROOT")
    assert 0 < mpld_pos < dedup_pos, (
        "MPLD fetch must run before the pre-split dedup"
    )


def test_mpld_excludes_control_and_is_downweighted():
    """MPLD is a CROP-domain supplement: it must be down-weighted in the
    source-weighted sampler (< 1.0), and it must import ONLY the phosphorus-
    deficiency folders ('-P', '-P50') while EXCLUDING the '_C' control
    (fully-fed = healthy) — importing control leaves would mislabel the
    nutrient_deficiency class exactly like a CoLeaf 'healthy' zip would."""
    src = _notebook_source()
    m = re.search(r"'mpld'\s*:\s*([0-9.]+)", _source_weights_block())
    assert m and float(m.group(1)) < 1.0, (
        "'mpld' must be down-weighted (<1.0) in SOURCE_WEIGHTS (crop domain gap)"
    )
    dirs = re.search(r"MPLD_DEFICIENCY_DIRS\s*=\s*\(([^)]*)\)", src)
    assert dirs, "MPLD_DEFICIENCY_DIRS table missing"
    block = dirs.group(1)
    assert "'-P'" in block and "'-P50'" in block, (
        "MPLD must import the '-P' and '-P50' deficiency folders"
    )
    assert "'_C'" not in block, (
        "MPLD must NOT list the '_C' control folder among deficiency dirs — "
        "control leaves are healthy and would mislabel nutrient_deficiency"
    )


def test_crop_supplements_capped_to_bound_prior_inflation():
    """v2.3.0 loaded ~1000 crop nutrient_deficiency rows, which inflated the
    class's logit-adjustment prior to head-class level (0.1165) and tanked
    in-domain recall (0.55->0.26). The crop caps at the fetch call sites must
    stay LOW so the class doesn't dominate (which is what drives the count-
    derived LA prior)."""
    src = _notebook_source()
    m = re.search(r"fetch_mpld_phosphorus_deficiency,\s*\n?\s*cap=(\d+)", src)
    assert m and int(m.group(1)) <= 300, (
        "MPLD cap must be reduced (<=300) to bound crop dominance of nutrient_deficiency"
    )
    # CoLeaf is no longer fetched (Mendeley 403s from Colab), so there is no
    # CoLeaf cap to bound — MPLD is the only active crop-nutrient supplement.


def test_la_prior_decoupled_from_crop_sampler_weight():
    """The logit-adjustment prior must track DEPLOYMENT frequency, not the
    crop-inflated training count. The crop source (mpld) must enter the
    LA prior at a SMALLER weight than its 0.5 sampler weight, and the LA
    prior helper must be fed PRIOR_SOURCE_WEIGHTS (not the raw sampler
    SOURCE_WEIGHTS) — otherwise nutrient_deficiency is treated as a head
    class and suppressed at inference."""
    src = _notebook_source()
    assert "PRIOR_SOURCE_WEIGHTS" in src, "LA prior is not decoupled from the sampler"
    assert re.search(
        r"class_priors_from_source_weights\(\s*train_ds,\s*PRIOR_SOURCE_WEIGHTS", src), (
        "the LA prior must be computed from PRIOR_SOURCE_WEIGHTS, not SOURCE_WEIGHTS"
    )
    for crop in ("mpld",):
        m = re.search(rf"PRIOR_SOURCE_WEIGHTS\['{crop}'\]\s*=\s*([0-9.]+)", src)
        assert m and float(m.group(1)) < 0.5, (
            f"PRIOR_SOURCE_WEIGHTS['{crop}'] must be < its 0.5 sampler weight "
            f"(de-inflate the deployment prior while still training on the signal)"
        )


def test_prefer_fp32_switch_ships_fp32_graph():
    """This deployment has CPU latency headroom and v2.3.0's INT8 showed a
    significant drift, so the export must support shipping FP32: a PREFER_FP32
    switch that removes classifier_int8.onnx and ships the FP32 classifier.onnx
    (which classifier.py then loads as the only ONNX artefact)."""
    src = _notebook_source()
    assert "PREFER_FP32" in src, "no PREFER_FP32 switch in the export"
    assert re.search(r"_ship_fp32\s*=\s*PREFER_FP32\s*or\s*\(drift\s*>\s*FAIL_DRIFT\)", src), (
        "the ship gate must honour PREFER_FP32 (OR the existing drift>FAIL gate)"
    )


def test_inat_physiological_label_noise_controls():
    """Report Stage 2: clean the noisy iNat free-text labels without
    corrupting data. The physiological queries must be constrained to
    iconic_taxa='Plantae' (drops the non-plant matches whose common names
    share words like "scorch" / "chlorosis"). The gray-mold free-text path
    must NOT force Plantae (Botrytis is legitimately filed under the fungus
    taxon)."""
    src = _notebook_source()
    # The physiological queries must restrict to iconic_taxa='Plantae'.
    assert re.search(
        r"_inat_freetext_observations\([^)]*iconic_taxa='Plantae'\)", src
    ), "physiological queries must restrict to iconic_taxa='Plantae'"
    # Gray-mold free-text must stay un-restricted (fungus iconic taxon):
    # the gray-mold-serving _fetch_inat_freetext_class must call the search
    # WITHOUT iconic_taxa (the plain 2-arg form).
    gm_fn = src[src.find("def _fetch_inat_freetext_class"):]
    gm_fn = gm_fn[: gm_fn.find("\ndef ", 1)]
    assert "iconic_taxa" not in gm_fn, (
        "gray-mold free-text must NOT force iconic_taxa=Plantae (Botrytis is a fungus)"
    )
    assert re.search(r"_inat_freetext_observations\(q, per_query\)", gm_fn), (
        "gray-mold path should use the un-restricted 2-arg free-text search"
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


def test_kaggle_houseplant_supplement_is_train_only_and_failsoft():
    """The Kaggle indoor-houseplant supplement must (a) exist, (b) be
    fail-soft on missing credentials/package, (c) DROP unmapped folders so an
    unexpected class can't corrupt the taxonomy, (d) be TRAIN-ONLY (its
    'kaggle_houseplant' source in no make_*_split allowed_sources), and (e)
    run before the pre-split dedup so its rows are deduped + scoped."""
    src = _notebook_source()
    assert "def fetch_kaggle_houseplant_disease" in src, "Kaggle fetcher missing"
    assert "def _kaggle_download" in src and "kagglehub" in src, (
        "Kaggle download helper (kagglehub) missing"
    )
    # Fail-soft on missing credentials.
    assert "KAGGLE_USERNAME" in src and "KAGGLE_KEY" in src, (
        "Kaggle helper must check for credentials and fail soft when absent"
    )
    assert re.search(r"return None", src), "Kaggle helper must return None on failure"
    # Unmapped/ambiguous folders dropped, not guessed. Mapping is keyword-based
    # on the <Species>_<Condition> folder names, with a DROP list checked first
    # (rust morphology, nutrient toxicity != deficiency, vascular bacterial wilt).
    assert "def _kaggle_map_folder" in src, "keyword-based Kaggle folder resolver missing"
    assert "KAGGLE_DROP_KEYWORDS" in src and "KAGGLE_CONDITION_KEYWORDS" in src, (
        "Kaggle mapping must use a drop-list + ordered condition keywords"
    )
    for must_drop in ("'rust'", "'toxicity'", "'bacterial_wilt'"):
        assert must_drop in src, f"Kaggle DROP list must contain {must_drop}"
    # Train-only: 'kaggle_houseplant' must never be an eval allowed source.
    for split_fn in ("make_val_realworld", "make_val_indomain",
                     "make_temperature_cal", "make_conformal_cal"):
        body = re.search(rf"def {split_fn}\(.*?\n(?:.*\n){{0,14}}", src)
        assert body and "kaggle" not in body.group(0), (
            f"{split_fn} must NOT allow the 'kaggle_houseplant' source — it is a "
            f"TRAIN-ONLY supplement until an operator confirms a real-photo slice"
        )
    # Runs before the pre-split dedup.
    kaggle_pos = src.find("_try_fetch('kaggle_houseplant'")
    dedup_pos = src.find("_dedup_train_in_place(TRAIN_ROOT")
    assert 0 < kaggle_pos < dedup_pos, (
        "Kaggle fetch must run before the pre-split dedup"
    )


def test_kaggle_houseplant_downweighted_and_prior_decoupled():
    """Like the CoLeaf/MPLD crop supplements, the Kaggle source (unverified
    studio-vs-real mix) must be down-weighted in the sampler (< 1.0) and enter
    the LA prior at a SMALLER weight than its sampler weight so it teaches
    signal without inflating the deployment prior."""
    src = _notebook_source()
    m = re.search(r"'kaggle_houseplant'\s*:\s*([0-9.]+)", _source_weights_block())
    assert m and float(m.group(1)) < 1.0, (
        "'kaggle_houseplant' must be down-weighted (<1.0) in SOURCE_WEIGHTS"
    )
    p = re.search(r"PRIOR_SOURCE_WEIGHTS\['kaggle_houseplant'\]\s*=\s*([0-9.]+)", src)
    assert p and float(p.group(1)) < float(m.group(1)), (
        "PRIOR_SOURCE_WEIGHTS['kaggle_houseplant'] must be < its sampler weight"
    )


def test_kaggle_strawberry_graymold_removed_entirely():
    """The Kaggle strawberry-disease set was an AGRICULTURAL Botrytis proxy
    (strawberry fruit/leaves), domain-mismatched from houseplants, so it is
    removed: gray_mold_botrytis draws from iNat (non-foliage filtered) + DoctorP
    + the supplement instead. No fetcher, slug, opt-in flag, or sampler/prior
    weight may remain — dead code/config invites a re-add of cross-domain
    imagery into the hardest-to-supply class. The PlantVillage 'Strawberry___*'
    label-map entries are a SEPARATE system (whole-dataset label remapping) and
    intentionally stay."""
    src = _notebook_source()
    assert "def fetch_kaggle_strawberry_graymold" not in src, "the strawberry fetcher must be gone"
    assert "_is_graymold_name" not in src, "the strawberry gray-mold helper must be gone"
    assert "KAGGLE_STRAWBERRY_SLUG" not in src, "the strawberry slug constant must be gone"
    assert "INCLUDE_KAGGLE_STRAWBERRY_GM" not in src, "the strawberry opt-in flag must be gone"
    assert "_try_fetch('kaggle_strawberry" not in src, "strawberry must never be fetched"
    assert re.search(r"'kaggle_strawberry'\s*:", src) is None, "no 'kaggle_strawberry' SOURCE_WEIGHTS entry"
    assert "PRIOR_SOURCE_WEIGHTS['kaggle_strawberry']" not in src, "no 'kaggle_strawberry' prior weight"


def test_kaggle_credentials_support_new_token_format_and_upload():
    """Credentials must support Kaggle's NEW token format (KAGGLE_API_TOKEN env
    / ~/.kaggle/access_token) in addition to the legacy username+key, and the
    notebook must offer an upload helper that installs whichever file the user
    provides (the new one-line token or a legacy kaggle.json)."""
    src = _notebook_source()
    # New-format detection in the downloader.
    assert "KAGGLE_API_TOKEN" in src and "access_token" in src, (
        "_kaggle_download must recognise the new-format token "
        "(KAGGLE_API_TOKEN / access_token), not only legacy username+key"
    )
    # Upload helper exists and handles both formats.
    assert "def kaggle_install_api_config" in src, "API config upload helper missing"
    assert "from google.colab import files" in src, (
        "upload helper must use the Colab file uploader"
    )
    assert "kaggle.json" in src and "access_token" in src, (
        "upload helper must install either the new access_token or legacy kaggle.json"
    )
    # An opt-in cell that doesn't block a plain Run-all by default.
    assert re.search(r"KAGGLE_UPLOAD_CREDS\s*=\s*False", src), (
        "the upload trigger must default to False so Run-all never blocks on a dialog"
    )
    # Colab secrets are not auto-exported as env vars, so a KAGGLE_API_TOKEN
    # secret must be pulled in explicitly via google.colab.userdata before the
    # credential check, or a set secret reads as "no credentials".
    assert "def _kaggle_load_colab_config" in src and "from google.colab import userdata" in src, (
        "must bridge a Colab KAGGLE_API_TOKEN secret into the environment "
        "(google.colab.userdata) — secrets are not auto-exported as env vars"
    )


def test_classifier_exports_embedding_as_second_output():
    """Report P1 (embedding OOD): the classifier ONNX must export the DINOv2
    CLS embedding as a SECOND output so the server can compute an
    embedding-space k-NN OOD score without a second model. output[0] must
    stay the logits so every existing run(...)[0] call site is unaffected."""
    src = _notebook_source()
    assert "_ExportWithEmbedding" in src, "embedding-export wrapper missing"
    assert re.search(r"output_names\s*=\s*\[\s*'logits'\s*,\s*'embedding'\s*\]", src), (
        "ONNX export must declare a 2nd 'embedding' output after 'logits'"
    )
    # The wrapper must return (head(feats), feats) — logits first, embedding
    # second — so the order matches the output_names above.
    assert re.search(r"return\s+self\.clf\.head\(feats\)\s*,\s*feats", src), (
        "the export wrapper must return (logits, embedding) in that order"
    )


def test_embedding_ood_is_calibrated_and_shipped():
    """Report P1: the embedding-space OOD bank + threshold must be calibrated
    (bank from one split, threshold on a DISJOINT split at TARGET_PLANT_FPR),
    shipped as ood_embedding.npz, and referenced from classes.json
    ood.embedding so the runtime ood.py can load it."""
    src = _notebook_source()
    assert "Embedding-space OOD (k-NN) calibration" in src, "embedding OOD block missing"
    # Bank from conformal_cal, threshold on temperature_cal (disjoint splits).
    assert "_collect_embeddings(eval_sess, cal_dl)" in src, "OOD bank not built from conformal_cal"
    assert "_collect_embeddings(eval_sess, temp_cal_dl)" in src, (
        "OOD threshold not calibrated on the disjoint temperature_cal split"
    )
    assert re.search(r"np\.quantile\(_ps,\s*1\.0\s*-\s*TARGET_PLANT_FPR\)", src), (
        "OOD threshold must be the (1 - TARGET_PLANT_FPR) quantile of in-dist scores"
    )
    # Shipped artefact + classes.json wiring.
    assert "np.savez_compressed('models/ood_embedding.npz'" in src, "OOD bank not written"
    assert "'embedding': EMBEDDING_OOD" in src, (
        "classes.json ood section must carry the embedding OOD config"
    )


def test_detector_crop_diagnostic_reports_whole_frame_fold():
    """Report P0 (detector-crop -0.093): the production-faithful diagnostic
    must report the crop+whole-frame FOLD alongside crop-only and whole-image
    so a Colab run directly verifies the pipeline arbitration recovers points."""
    src = _notebook_source()
    assert "crop+whole_fold" in src, "detector-crop diagnostic doesn't report the fold"
    assert "_yp_fold" in src and "fold_disease_macro_f1" in src, (
        "the folded prediction + its logged F1 must be present"
    )


def test_deploy_bundle_includes_ood_npz_and_excludes_torch_checkpoints():
    """The §6b deploy archive must include the embedding-OOD bank (.npz) that
    classes.json -> ood.embedding references — omitting it silently disabled the
    embedding OOD gate at serve time in v2.3.1. The ONNX-only runtime cannot load
    the torch checkpoints (.pt/.pth), so those must NOT bloat the deploy bundle."""
    src = _notebook_source()
    assert "_bound['ood_embedding'] = 'ood_embedding.npz'" in src
    assert "for _role, _spec in sorted(_ship_artifacts.items())" in src
    assert "_verified_manifest_artifact(_role, _spec)" in src
    assert "_tar.add(_p, arcname=_arc)" in src
    assert "_tar.add(Path('models/detector.pt')" not in src
    assert "_tar.add(Path('models/classifier.pth')" not in src
    # A declared bank is a hard integrity failure, never a warning/fallback.
    assert "references OOD bank" in src and "not bound by manifest" in src


def test_nutrient_deficiency_supplied_without_field_crop_sources():
    """nutrient_deficiency must keep live supply that is NOT field-crop imagery.

    History: a Colab-reachable Kaggle rice N/P/K set once filled this class,
    because the Mendeley CoLeaf source is intermittently IP-blocked from Colab.
    The 2026-06-19 data-quality audit gated it off — field-crop imagery was
    feeding the class with the worst precision, the very class the supplement
    is being de-cropped to fix — and 2.6.13 removed the dead fetcher outright.

    This replaces an earlier test that asserted the *fetcher existed*. That
    check kept passing after the source was disabled, so it certified a
    contributor of zero images. Assert the wiring and the exclusion instead.
    """
    src = _notebook_source()
    assert "def fetch_kaggle_nutrient_deficiency" not in src, (
        "the rice/field-crop nutrient fetcher was dropped by the 2026-06-19 audit; "
        "do not reintroduce it without revisiting nutrient_deficiency precision"
    )
    assert "_try_fetch('kaggle_nutrient_deficiency'" not in src, (
        "a field-crop nutrient source must not be wired into the fetch orchestration"
    )
    # ...but the class must still have live, non-crop supply actually wired.
    for tag in ("mpld_phosphorus_deficiency", "inat_physiological"):
        assert f"_try_fetch('{tag}'" in src, (
            f"{tag} must stay wired — it is nutrient_deficiency's remaining supply"
        )


def test_coco_download_prefers_hf_mirror_first():
    """images.cocodataset.org serves a broken cert; the fetch must try the HF
    mirror FIRST (no wasted SSL retries / alarming log) and fall back to the
    canonical host only if the mirror is down."""
    src = _notebook_source()
    m = re.search(r"def _coco_download\(.*?\n(?:.*\n){2,20}", src)
    assert m, "_coco_download not found"
    body = m.group(0)
    hf_pos = body.find("hf_hub_download(_COCO_HF_MIRROR")
    canon_pos = body.find("http_download(canonical_url")
    assert 0 < hf_pos < canon_pos, (
        "COCO must attempt the HF mirror before the canonical cocodataset.org URL"
    )


def test_ood_floor_is_calibrated_not_hardcoded():
    """audit fix #2: classes.json max_logit_floor must be computed from
    the MaxLogit distribution on plants vs NAP, not hardcoded to 4.0."""
    src = _notebook_source()
    # Calibration block must exist
    assert "TARGET_PLANT_FPR" in src, "audit fix #2 — calibration target missing"
    assert "_maxlogit_distribution" in src, "audit fix #2 — distribution helper missing"
    # And classes_payload must read ML_FLOOR (not the literal 4.0). The
    # served-gate block writes its own ``'max_logit_floor': _srv_ml_floor``
    # (fitted on the served statistic), so check every occurrence: one must
    # be the calibrated single-image floor and none may be a number.
    vals = re.findall(r"'max_logit_floor'\s*:\s*([A-Z_a-z0-9.]+)", src)
    assert vals, "max_logit_floor entry missing"
    assert any(v in ("ML_FLOOR", "ML_FLOOR_PERSISTED") for v in vals), (
        f"max_logit_floor values are {vals}, one must reference the calibrated "
        f"ML_FLOOR / ML_FLOOR_PERSISTED variable"
    )
    assert not any(re.fullmatch(r"[0-9.]+", v) for v in vals), (
        f"a max_logit_floor is hardcoded: {vals}"
    )


def test_maxlogit_floor_is_scored_over_plant_logits_only():
    """v2.6.13: the MaxLogit floor must exclude the ``not_a_plant`` logit.

    ``not_a_plant`` is a TRAINED class, so on a NAP photo the all-class max IS
    the confident NAP logit — its median sits above the plant median and the
    export's ``separation < 0`` branch disables the floor. That happened on every
    run on record (sep -0.939 / -0.403 / -0.199 / -0.104), leaving the NAP
    AND-gate with only the embedding bank. The fix is structural, not more hard
    negatives: score the max over the PLANT columns.
    """
    src = _notebook_source()
    assert "MAXLOGIT_SCOPE" in src, "MaxLogit scope switch missing"
    assert "_PLANT_LOGIT_COLS" in src, "plant-only logit column mask missing"
    assert re.search(r"logits\[i\]\[_PLANT_LOGIT_COLS\]\.max\(\)", src), (
        "_maxlogit_distribution must take the max over _PLANT_LOGIT_COLS, not "
        "over every logit — the all-class max cannot separate NAP from plants"
    )
    # The scope must ship in classes.json so the server scores the same quantity.
    assert re.search(r"'max_logit_scope'\s*:\s*MAXLOGIT_SCOPE", src), (
        "classes.json -> ood.max_logit_scope must be persisted; without it the "
        "server defaults to 'all_classes' and scores a different number than "
        "the floor was calibrated on"
    )


def test_required_source_floors_gate_missing_upstream_corpora():
    """v2.6.13: every load-bearing §2 source must have a non-zero floor.

    On 2026-07-20 the ornamental Kaggle slug was deleted upstream, returned 403,
    and ``_kaggle_download``'s fail-soft path dropped 2,264 TRAIN images with a
    single 'skipping' line. Because that source is TRAIN-ONLY the val_realworld
    ship gate could not see it and PASSED; val_indomain fell 0.865 -> 0.809. A
    silent corpus change also voids every version-over-version A/B.
    """
    src = _notebook_source()
    assert "REQUIRED_SOURCE_FLOORS" in src, "source-floor gate missing"
    block = re.search(r"REQUIRED_SOURCE_FLOORS = \{(.*?)\n\}", src, re.DOTALL)
    assert block, "REQUIRED_SOURCE_FLOORS dict literal not found"
    floors = {
        k: float(v)
        for k, v in re.findall(r"'([a-z_]+)'\s*:\s*([0-9.]+)", block.group(1))
    }
    # The sources whose loss is invisible to the ship gate (train-only) or that
    # carry a whole class are the ones this gate exists for.
    for must_guard in ("kaggle_ornamental", "plantdoc", "plantvillage", "inat",
                       "kaggle_houseplant", "inat_pest"):
        assert floors.get(must_guard, 0) > 0, (
            f"'{must_guard}' has no positive floor — a silent upstream loss "
            f"would train a model that is not comparable to any other version"
        )
    # DoctorP (real app photos, the starved-class supply) is guarded
    # unconditionally: licences are recorded, never gated (2026-09-21).
    assert floors.get("doctorp", 0) >= 1000 and floors.get("doctorp_pepper", 0) > 0
    assert "ALLOW_NONCOMMERCIAL_RESEARCH_SOURCES" not in src
    # It must actually raise, not just warn, by default.
    assert re.search(r"STRICT_SOURCE_FLOORS\s*=\s*True", src), (
        "STRICT_SOURCE_FLOORS must default to True (fail-fast over fail-soft)"
    )
    assert "SOURCE INVENTORY BELOW FLOOR" in src, "gate must raise a RuntimeError"
    # And the inventory must ship with the model card so an A/B can diff corpora.
    assert re.search(r"'source_inventory'\s*:", src), (
        "model_card must carry the per-source inventory — class counts alone "
        "cannot show WHICH corpus a run trained on"
    )


def test_weakclass_precision_is_measured_shipped_and_selectable():
    """v2.5: the physiological classes are PRECISION-bound (they over-trigger),
    so precision must be (a) reported by the eval helper and (b) shipped with a
    bootstrap CI in the model card next to the recall CI — both UNCONDITIONAL, so
    the over-trigger is always visible. (c) A precision-aware checkpoint
    selection lever exists (macro-F1 + weight * weak-class F0.5 on val_indomain)
    but is OPT-IN, OFF by default (weight 0.0 == proven pure-macro-F1 selection),
    because the precision/recall trade is not yet GPU-A/B-validated to transfer
    to the val_realworld ship metric."""
    src = _notebook_source()
    # (a) the eval helper returns per-class precision
    assert "'per_class_precision'" in src, "evaluate() must report per_class_precision"
    # (b) a precision bootstrap CI is computed and shipped in the model card
    assert "_per_class_precision_ci" in src, "per-class precision CI helper missing"
    assert "per_class_precision_ci_realworld" in src, (
        "model card must ship the weak-class precision CI beside the recall CI"
    )
    # (c) the selection lever exists, is F0.5-based (recall-aware, so a degenerate
    # predict-once epoch earns no bonus), and DEFAULTS TO OFF (pure macro-F1).
    assert "_weakclass_fbeta" in src, "the F0.5 weak-class selection metric is missing"
    assert re.search(r"sel\s*=\s*f1\s*\+\s*SELECT_WEAKCLASS_PRECISION_WEIGHT", src), (
        "the selection score must be macro-F1 + weight * weak-class F0.5"
    )
    assert re.search(r"SELECT_WEAKCLASS_PRECISION_WEIGHT\s*=\s*0\.0\b", src), (
        "the precision-aware selection lever must default to 0.0 (pure macro-F1) "
        "until the precision/recall trade is GPU-A/B-validated"
    )


def test_class_logit_bias_validation_runs_after_mode_definition():
    src = _notebook_source()
    mode = src.index("TRAIN_MODES = {")
    family = src.index("FAMILY_CONFIG = TRAIN_MODES[TRAIN_MODE]")
    guard = src.index("if CLASS_LOGIT_BIAS and FAMILY_CONFIG.get('distill')")
    assert mode < family < guard, (
        "a non-empty CLASS_LOGIT_BIAS must not reference TRAIN_MODES before definition"
    )


def test_kaggle_folder_identity_uses_full_relative_parent_paths():
    src = _notebook_source()
    assert "def _images_by_relative_parent" in src
    assert "p.parent.relative_to(root).as_posix()" in src
    assert "by_folder.setdefault(p.parent.name" not in src, (
        "same-named leaf folders from different branches must never be merged"
    )
    orn = re.search(r"def fetch_kaggle_ornamental_disease.*?(?=\ndef )", src, re.DOTALL)
    assert orn and "_orn_map_folder(norm, slug, rel_norm=norm)" in orn.group(0)


def test_supplement_joins_and_saves_by_complete_path():
    src = _notebook_source()
    body = re.search(r"def fetch_kaggle_supplement.*?(?=\n# 'pests')", src, re.DOTALL)
    assert body
    text = body.group(0)
    assert "Path(r['filename']).name" not in text
    assert "PurePosixPath(_raw)" in text and "duplicate path key" in text
    assert "STRICT_SUPPLEMENT_FILTER_VERDICT = False" in src
    assert "STRICT_SUPPLEMENT_LICENSE_PROVENANCE = False" in src
    assert "output_stem=_row['_disk_stem']" in text


def test_archive_import_sidecar_sets_required_source_field():
    src = _notebook_source()
    body = re.search(r"def _save_archive_member.*?(?=\n# )", src, re.DOTALL)
    assert body
    assert "source=source," in body.group(0)


def test_holdout_checks_same_and_cross_class_near_duplicates():
    src = _notebook_source()
    same = src.index("resolve_phash_split_leakage(HOLDOUT, (TRAIN_ROOT,), threshold=6)")
    cross = src.index("audit_cross_class_phash_leakage(HOLDOUT, (TRAIN_ROOT,), threshold=6")
    assert same < cross


def test_zero_support_holdout_classes_are_reported():
    src = _notebook_source()
    assert "by_class.get(c, 0) for c in CLASSES" in src


def test_public_image_sources_record_rights_but_never_gate_on_them():
    """Study-project policy (2026-09-21): per-image licence/provenance is
    written into every sidecar so a bundle can report what it trained on, and
    no fetcher, floor or bundler may drop a row or refuse a run because of it.
    The 2026-08-25 licence filters cost ~87 % of iNat's research-grade Plantae
    supply (83.0M vs 10.8M observations, measured against the live API) and
    all 1,558 DoctorP rows."""
    src = _notebook_source()
    # iNat: no API-side licence filter; every photo tallied, none rejected.
    assert "'photo_license'" not in src
    assert "_inat_photo_provenance" in src and "license_code" in src
    assert "_INAT_LICENSE_TALLY" in src and "_INAT_LICENSE_REJECTED" not in src
    assert "INAT_KNOWN_PHOTO_LICENSES.get(code)" in src
    # COCO / Open Images: recorded, never filtered.
    assert "COCO_ALLOWED_LICENSE_IDS" not in src and "_coco_tally(im)" in src
    assert "OI_ALLOWED_LICENSE_KEYS" not in src and "_OI_LICENSE_TALLY" in src
    assert "validation-images-with-rotation.csv" in src and "OriginalLandingURL" in src
    # Missing licences are reported, not fatal.
    assert "_RIGHTS_REQUIRED_SOURCES" in src and "RIGHTS PROVENANCE MISSING" not in src
    assert "'policy': 'study_project_record_only'" in src
    assert "'gated_on_license': False" in src


def test_doctorp_is_in_the_default_recipe_with_its_licence_recorded():
    src = _notebook_source()
    assert "ALLOW_NONCOMMERCIAL_RESEARCH_SOURCES" not in src
    assert "DOCTORP_LICENSE_ID = 'cc-by-nc-sa-4.0'" in src
    assert "license_id=DOCTORP_LICENSE_ID" in src
    assert "_try_fetch('doctorp', fetch_doctorp_disease, per_class_cap=400)" in src
    assert "RESEARCH-ONLY RUN: non-commercial data" not in src


def test_recipe_uses_deterministic_torch_settings():
    src = _notebook_source()
    assert "CUBLAS_WORKSPACE_CONFIG" in src
    assert "torch.backends.cudnn.benchmark = False" in src
    assert "torch.backends.cudnn.deterministic = True" in src
    assert "torch.use_deterministic_algorithms(True, warn_only=True)" in src


def test_supplement_eval_is_model_era_and_capture_family_aware():
    """The provenance columns are honoured when present and fall back to
    defaults when absent (v8 carries none); the strict knob is opt-in. Family
    conflicts are RESOLVED by routing the family as one unit, never raised —
    behaviour pinned in test_notebook_supplement.py."""
    src = _notebook_source()
    assert "STRICT_SUPPLEMENT_MODEL_PROVENANCE = False" in src
    assert "_SUPPLEMENT_PROVENANCE_FALLBACKS" in src
    assert "if not _first_seen or _first_seen == 'never':" in src
    assert "f'sha256:{hashlib.sha256(body).hexdigest()}'" in src
    assert "SUPPLEMENT_EVAL_THROUGH_MODEL_VERSION = MODEL_VERSION" in src
    assert "first_seen_model_version" in src and "immutable_source_id" in src
    assert "capture_family_id" in src
    assert "elif r.get('_seen_by_eval_model')" in src
    assert "SUPPLEMENT_REQUIRED_INDEPENDENCE_METHOD = 'capture_family_id+phash_v1'" in src
    assert "SUPPLEMENT_LOAD_UNLISTED_IMAGES = True" in src
    assert "_planned[_k] = ('supplement_train_only',) + tuple(_plan[1:])" in src


def test_private_v2_unseen_domain_set_is_diagnostic_not_ship_gate():
    src = _notebook_source()
    assert re.search(r"REQUIRE_OOD_BENCHMARK\s*=\s*False", src)
    assert "'diagnostic_only': True, 'release_blocking': False" in src
    assert "independent_family_count_verified" in src
    assert "OOD SHIP GATE PASSED" not in src


def test_unseen_domain_diagnostic_must_run_and_record_alerts():
    """Non-blocking is not the same as optional: the sentinel must be scored on
    every production run and its alerts must land in the artefact, not only in
    the Colab log."""
    src = _notebook_source()
    assert re.search(r"REQUIRE_OOD_DIAGNOSTIC_RUN\s*=\s*True", src)
    assert "raise RuntimeError(\n            'OOD DIAGNOSTIC NOT RUN: '" in src
    assert "ood_benchmark['alerts'] = list(_ood_fail)" in src
    assert "log_run({'phase': 'ood_diagnostic'" in src


def test_aqua_sidecar_declares_preprocessing_and_logit_semantics():
    src = _notebook_source()
    aqua = re.search(
        r"\(AQUA_DIR / 'classes\.json'\).*?(?=print\(f'Aqua TFLite written)",
        src, re.DOTALL)
    assert aqua
    assert "'eval_resize_ratio': EVAL_RESIZE_RATIO" in aqua.group(0)
    assert "'output_semantics': 'logits'" in aqua.group(0)


def test_aqua_sidecar_carries_a_maxlogit_ood_floor_fit_on_served_preprocessing():
    """§5 fits Aqua's OOD floor like §6's: plant-only scope, 5th percentile of
    temperature_cal plants, the app's resize + centre crop, off when it does
    not separate from not_a_plant; the sidecar carries it as ``ood``."""
    src = _notebook_source()
    assert "AQUA_OOD_TARGET_PLANT_FPR = 0.05" in src
    assert "_aq_scope = 'plant_only' if _aq_nap is not None else 'all_classes'" in src
    assert "T.Resize(int(STUDENT_IMG * EVAL_RESIZE_RATIO)), T.CenterCrop(STUDENT_IMG)" in src
    assert "_aqua_served_logits(TEMP_CAL)" in src
    assert "elif _aq_sep < 0:" in src
    aqua = re.search(
        r"\(AQUA_DIR / 'classes\.json'\).*?(?=print\(f'Aqua TFLite written)",
        src, re.DOTALL)
    assert aqua and "'ood': AQUA_OOD" in aqua.group(0)


def test_aqua_export_requires_one_float32_graph_and_never_substitutes_quantized():
    src = _notebook_source()
    assert "_float32_emitted = [p for p in _emitted if 'float32' in p.name.lower()]" in src
    assert "if len(_float32_emitted) != 1:" in src
    assert "_chosen = _float32_emitted[0]" in src
    assert "Aqua serving contract requires FLOAT32 I/O" in src
    assert "Aqua FLOAT32 TFLite parity OK" in src
    assert "_tflite_rank" not in src
    assert "combined run aborted: Aqua exact served-artifact validation failed" in src


def test_export_emits_checksummed_single_graph_serving_manifest():
    src = _notebook_source()
    assert "Path('models/bundle_manifest.json').write_text" in src
    assert "'fallback_allowed': False" in src
    assert "_selected_classifier = ('classifier_int8.onnx' if int8_shipped" in src
    assert "'calibration_source': _expected_fit_source" in src
    for bound in ("'classifier'", "'detector'", "'classes'", "'temperature'",
                  "'conformal'"):
        assert bound in src
    for contract in ("'padding_ratio'", "'max_crops'", "'min_confidence'",
                     "'nms_iou'", "'max_detections'",
                     "'fold_whole_frame_vote'", "'output_semantics'",
                     "'variance_laplacian_floor'", "'detector_confidence_floor'"):
        assert contract in src


def test_notebook_run_boundary_is_fresh_gpu_only_and_manifest_owned():
    src = _notebook_source()
    assert "if not torch.cuda.is_available():" in src
    assert "CUDA GPU required for training" in src
    assert "RUN_LOG.write_text('', encoding='utf-8')" in src
    assert "exist_ok=False" in src and "DETECTOR_RUN_DIR" in src
    assert "next(Path('runs/detect').rglob('best.pt'))" not in src
    assert "AQUA_EXPORT_SUCCEEDED = True" in src
    assert "combined run aborted" in src
    assert "for _p in sorted(Path('models').iterdir())" not in src
    assert "for _role, _spec in sorted(_ship_artifacts.items())" in src


def test_notebook_binds_data_run_and_backbone_revision():
    src = _notebook_source()
    assert "DINOV2_REPO_REF = 'facebookresearch/dinov2:" in src
    assert "torch.hub.load(DINOV2_REPO_REF" in src
    assert "classification_sha256" in src
    assert "Path('models/data_manifest.json').write_text" in src
    assert "Path('models/run_manifest.json').write_text" in src
    assert "'data_manifest': 'data_manifest.json'" in src
    assert "'run_manifest': 'run_manifest.json'" in src


def test_notebook_binds_mutable_sources_weights_and_colab_environment():
    src = _notebook_source()
    assert "PACKAGE_RESOLUTION_POLICY = 'colab_cuda_abi_runtime_resolved_manifest_bound_v1'" in src
    assert "_importlib_metadata.distributions()" in src
    assert "'recipe_environment_sha256': _recipe_environment_sha256" in src
    assert "_hf_resolve_dataset_revision(name)" in src
    assert "revision=revision, token=_HF_AUTH" in src
    assert "'huggingface_revisions'" in src
    assert "YOLO_BASE_WEIGHT_IDENTITY = {" in src
    assert "'sha256': sha256_file(_base_path)" in src
    assert "'detector_base_weights': YOLO_BASE_WEIGHT_IDENTITY" in src


def test_kaggle_latest_revision_is_recorded_after_download():
    """Mutable latest is deliberate, but completed runs remain auditable."""
    src = _notebook_source()
    assert "KAGGLE_REVISION_SELECTION_POLICY = 'latest_at_download_time'" in src
    assert "kagglehub.dataset_download(slug)" in src
    assert "KAGGLE_EXPECTED_REVISIONS" not in src
    assert "'selection_policy': KAGGLE_REVISION_SELECTION_POLICY" in src
    assert "'kaggle_version': _resolved_version" in src
    assert "'kaggle': dict(sorted(_KAGGLE_RESOLVED_REVISIONS.items()))" in src


def test_notebook_documents_kaggle_fail_soft_but_production_required():
    raw = NB_PATH.read_text(encoding="utf-8")
    assert "Kaggle credentials (required by the default production recipe)" in raw
    assert "Individual Kaggle fetchers fail soft" in raw
    assert "REQUIRE_SUPPLEMENT=True" in raw


def test_historical_archive_narrative_is_explicitly_superseded():
    src = _notebook_source()
    assert "SUPERSEDED 2.7.3 ARCHIVE NARRATIVE (DO NOT USE AS EVIDENCE)" in src
    assert "archive-derived counts such as 3/31 -> 4/31" in src
    assert "Those historical point estimates, p-values and" in src
    assert "pseudoreplicates" in src


def test_evaluation_contract_documents_manifest_only_deploy_and_advisory_ood():
    markdown = _notebook_markdown()
    section = markdown.split("## §7 — Evaluation contract", 1)[1]
    assert "`fallback_allowed=false`" in section
    assert "exactly the graph that passed drift/calibration; never both" in section
    assert "`classifier.pth`, `detector.pt`, `classifier_fp32.onnx`" in section
    assert "are **not deploy artefacts**" in section
    assert "`REQUIRE_OOD_BENCHMARK=False`" in section
    assert "`served_gate_enabled=false`" in section
    assert "resample independent capture families" in section


def test_server_sidecar_declares_complete_classifier_preprocessing():
    src = _notebook_source()
    block = re.search(r"classes_payload = \{.*?# ── Inference benchmark", src, re.DOTALL)
    assert block
    for declaration in ("'layout': 'NCHW'", "'normalize_mean'", "'normalize_std'",
                        "'eval_resize_ratio': EVAL_RESIZE_RATIO",
                        "'output_semantics': 'logits'"):
        assert declaration in block.group(0)


def test_supplement_accepts_filter_tool_verdict_vocabulary():
    """``tools/filter_supplement.py`` writes the ``filter_verdict`` column; §2
    must accept every verdict it can emit, and reject a byte/manifest mismatch."""
    src = _notebook_source()
    for verdict in ("dup_intra", "dup_cross", "tiny", "corrupt", "missing",
                    "phash_mismatch", "bench_leak", "keep"):
        assert f"'{verdict}'" in src.split("_KNOWN_FILTER_VERDICTS = {", 1)[1].split("}", 1)[0]
    assert "_filter_counts.get('phash_mismatch', 0)" in src


def test_supplement_family_cannot_straddle_eval_and_train():
    src = _notebook_source()
    assert "_family_sources.setdefault(_family_id, set()).add(_src)" in src
    assert "if 'supplement' in s and len(s) > 1" in src


def test_ood_diagnostic_accepts_builder_manifest_filename():
    src = _notebook_source()
    assert "next(_ood_root.rglob('manifest.csv'), None)" in src


def test_served_ood_gate_is_calibrated_on_the_served_statistic():
    """2.7.5: the overlay pipeline.py applies is fitted in §6 through the same
    detector -> crop -> whole-frame-fold path it serves, and the crop/detector
    rules the notebook mirrors are the ones pipeline.py / detector.py run."""
    import detector as detector_module
    import pipeline as pipeline_module

    src = _notebook_source()
    assert "SERVED-PATH OOD calibration" in src
    assert "'calibration_mode': 'full_served_pipeline'" in src
    assert "'embedding_aggregation': 'min_member_distance'" in src
    assert "'max_logit_aggregation': 'max_member_logit'" in src
    assert "'served_gate': SERVED_GATE" in src
    assert "np.quantile(_emb_p, 1.0 - TARGET_PLANT_FPR)" in src
    assert "neither served signal separates" in src
    crop = re.search(r"SERVED_GATE_CROP = \{(.*?)\}", src, re.DOTALL)
    assert crop
    got = dict(re.findall(r"'([a-z_]+)':\s*([0-9.]+|True|False)", crop.group(1)))
    real = getattr(pipeline_module, "_RealPipelineForTests", pipeline_module.PlantDiseasePipeline)
    assert int(got["max_crops"]) == real._MAX_RECLASSIFY
    assert int(got["min_size_px"]) == pipeline_module.MIN_CROP_SIZE
    assert float(got["min_area_fraction"]) == pipeline_module.MIN_BBOX_AREA_FRAC
    assert float(got["padding_ratio"]) == pipeline_module.CROP_PADDING_RATIO
    det = re.search(r"SERVED_GATE_DETECTOR = \{(.*?)\}", src, re.DOTALL)
    assert det
    dgot = dict(re.findall(r"'([a-z_]+)':\s*([0-9.]+)", det.group(1)))
    assert int(dgot["img_size"]) == detector_module.DETECTOR_IMG_SIZE
    assert float(dgot["min_confidence"]) == detector_module.MIN_CONFIDENCE
    assert int(dgot["max_detections"]) == detector_module.MAX_DETECTIONS
    floor = re.search(r"SERVED_GATE_DET_CONF_FLOOR = ([0-9.]+)", src)
    assert floor and float(floor.group(1)) == pipeline_module.NAP_DETECTOR_CONF_FLOOR
    # The manifest reads the same dicts, so the mirror cannot drift from the contract.
    assert "**SERVED_GATE_CROP," in src and "**SERVED_GATE_DETECTOR," in src


def test_conformal_sidecar_declares_its_coverage_basis():
    src = _notebook_source()
    assert "cfg['coverage_basis'] = (" in src
    assert "'audit_target'" in src and "'nominal_one_minus_alpha'" in src
    assert "best['target_coverage'] = CONF_TARGET_COVERAGE" in src
