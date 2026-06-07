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

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NB_PATH = REPO_ROOT / "server" / "train_notebook.ipynb"


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
    assert "model.backbone.eval()" in src, (
        "backbone must stay in eval() during head/LoRA training"
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
    (MobileNetV3-Small, §5), gated behind the aqua family so it never touches
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
    # KD is gated behind the aqua family's distill flag, not run unconditionally.
    assert "FAMILY_CONFIG.get('distill')" in src, (
        "Aqua KD must be gated behind the aqua family's distill flag"
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
    # CoLeaf is intentionally NOT actively fetched (Mendeley unreachable). The
    # function may remain as a documented restore-path, but the call is gone so
    # it can't 403-spam every run.
    assert "_try_fetch('coleaf_nutrient_deficiency'" not in src, (
        "CoLeaf must NOT be fetched — Mendeley 403s from Colab; mirror it to "
        "Kaggle to restore"
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


def test_coleaf_is_downweighted_and_excludes_healthy():
    """CoLeaf is a CROP-domain supplement: it must be down-weighted in the
    source-weighted sampler (< 1.0) so it doesn't pull the prior off the
    houseplant target, and it must import only unambiguous single-nutrient
    deficiency zips — never the 'healthy' or mixed 'more-deficiencies' zips
    (which would mislabel the nutrient_deficiency class)."""
    src = _notebook_source()
    m = re.search(r"'coleaf'\s*:\s*([0-9.]+)", src)
    assert m and float(m.group(1)) < 1.0, (
        "'coleaf' must be down-weighted (<1.0) in SOURCE_WEIGHTS (crop domain gap)"
    )
    zips_block = re.search(r"COLEAF_DEFICIENCY_ZIPS\s*=\s*\{.*?\}", src, re.DOTALL)
    assert zips_block, "COLEAF_DEFICIENCY_ZIPS table missing"
    lowered = zips_block.group(0).lower()
    assert "healthy" not in lowered and "more-deficiencies" not in lowered, (
        "CoLeaf import must exclude healthy / mixed zips — every imported row "
        "must be an unambiguous single-nutrient deficiency"
    )


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
    m = re.search(r"'mpld'\s*:\s*([0-9.]+)", src)
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
    crop-inflated training count. Crop sources (coleaf, mpld) must enter the
    LA prior at a SMALLER weight than their 0.5 sampler weight, and the LA
    prior helper must be fed PRIOR_SOURCE_WEIGHTS (not the raw sampler
    SOURCE_WEIGHTS) — otherwise nutrient_deficiency is treated as a head
    class and suppressed at inference."""
    src = _notebook_source()
    assert "PRIOR_SOURCE_WEIGHTS" in src, "LA prior is not decoupled from the sampler"
    assert re.search(
        r"class_priors_from_source_weights\(\s*train_ds,\s*PRIOR_SOURCE_WEIGHTS", src), (
        "the LA prior must be computed from PRIOR_SOURCE_WEIGHTS, not SOURCE_WEIGHTS"
    )
    for crop in ("coleaf", "mpld"):
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
    m = re.search(r"'kaggle_houseplant'\s*:\s*([0-9.]+)", src)
    assert m and float(m.group(1)) < 1.0, (
        "'kaggle_houseplant' must be down-weighted (<1.0) in SOURCE_WEIGHTS"
    )
    p = re.search(r"PRIOR_SOURCE_WEIGHTS\['kaggle_houseplant'\]\s*=\s*([0-9.]+)", src)
    assert p and float(p.group(1)) < float(m.group(1)), (
        "PRIOR_SOURCE_WEIGHTS['kaggle_houseplant'] must be < its sampler weight"
    )


def test_kaggle_strawberry_graymold_is_train_only_proxy():
    """gray_mold_botrytis is supply-bound at the iNat ceiling, so a crop-domain
    Botrytis proxy (the Kaggle strawberry COCO set) supplements it. Must (a)
    parse the gray-mold category, (b) write into gray_mold_botrytis, (c) be
    TRAIN-ONLY (no make_*_split allows 'kaggle_strawberry'), and (d) be
    down-weighted with the LA prior decoupled below the sampler weight."""
    src = _notebook_source()
    assert "def fetch_kaggle_strawberry_graymold" in src, "strawberry gray-mold fetcher missing"
    assert "_is_graymold_name" in src and "botrytis" in src.lower(), (
        "must detect the gray-mold / Botrytis category"
    )
    # The real dataset ships LabelMe per-image JSON (shapes[].label), not a single
    # COCO file — the fetcher must handle that schema (validated: 621 gray-mold imgs).
    assert "shapes" in src and "imagePath" in src, (
        "strawberry fetcher must parse the LabelMe per-image JSON schema (shapes[].label)"
    )
    assert "'gray_mold_botrytis'" in src, "must write into the gray_mold_botrytis class"
    for split_fn in ("make_val_realworld", "make_val_indomain",
                     "make_temperature_cal", "make_conformal_cal"):
        body = re.search(rf"def {split_fn}\(.*?\n(?:.*\n){{0,14}}", src)
        assert body and "kaggle_strawberry" not in body.group(0), (
            f"{split_fn} must NOT allow 'kaggle_strawberry' — it is a TRAIN-ONLY proxy"
        )
    sw = re.search(r"'kaggle_strawberry'\s*:\s*([0-9.]+)", src)
    assert sw and float(sw.group(1)) < 1.0, "'kaggle_strawberry' must be down-weighted (<1.0)"
    pr = re.search(r"PRIOR_SOURCE_WEIGHTS\['kaggle_strawberry'\]\s*=\s*([0-9.]+)", src)
    assert pr and float(pr.group(1)) < float(sw.group(1)), (
        "PRIOR_SOURCE_WEIGHTS['kaggle_strawberry'] must be < its sampler weight"
    )
    # Runs before the pre-split dedup.
    assert 0 < src.find("_try_fetch('kaggle_strawberry_graymold'") < src.find("_dedup_train_in_place(TRAIN_ROOT"), (
        "strawberry fetch must run before the pre-split dedup"
    )


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
    assert "def kaggle_upload_credentials" in src, "creds upload helper missing"
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
    assert "def _kaggle_sync_colab_secrets" in src and "from google.colab import userdata" in src, (
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
    m = re.search(r"_BUNDLE_SUFFIXES\s*=\s*\{([^}]*)\}", src)
    assert m, "_BUNDLE_SUFFIXES not found"
    suffixes = m.group(1)
    assert "'.npz'" in suffixes, "deploy bundle MUST include .npz (the embedding-OOD bank)"
    assert "'.onnx'" in suffixes and "'.json'" in suffixes
    assert "'.pth'" not in suffixes and "'.pt'" not in suffixes, (
        "torch checkpoints (.pt/.pth) are not loadable by the ONNX-only server "
        "and must not bloat the deploy bundle"
    )
    # And a guard that fails loud if a declared OOD bank isn't bundled.
    assert "references ood bank" in src and "embedding OOD will be DISABLED" in src, (
        "§6b must warn when classes.json declares an OOD bank absent from the archive"
    )


def test_kaggle_nutrient_supplement_replaces_mendeley_coleaf():
    """The Mendeley CoLeaf source is intermittently IP-blocked from Colab, so a
    Colab-reachable Kaggle nutrient source must supplement nutrient_deficiency.
    Must (a) exist with a concrete slug, (b) write into nutrient_deficiency,
    (c) be TRAIN-ONLY, (d) be down-weighted with the LA prior decoupled."""
    src = _notebook_source()
    assert "def fetch_kaggle_nutrient_deficiency" in src, "Kaggle nutrient fetcher missing"
    assert "KAGGLE_NUTRIENT_SLUG" in src and "nutrientdeficiencysymptomsinrice" in src, (
        "must wire a concrete Colab-reachable Kaggle nutrient slug"
    )
    assert "'nutrient_deficiency'" in src
    for split_fn in ("make_val_realworld", "make_val_indomain",
                     "make_temperature_cal", "make_conformal_cal"):
        body = re.search(rf"def {split_fn}\(.*?\n(?:.*\n){{0,14}}", src)
        assert body and "kaggle_nutrient" not in body.group(0), (
            f"{split_fn} must NOT allow 'kaggle_nutrient' — it is a TRAIN-ONLY proxy"
        )
    sw = re.search(r"'kaggle_nutrient'\s*:\s*([0-9.]+)", src)
    assert sw and float(sw.group(1)) < 1.0, "'kaggle_nutrient' must be down-weighted (<1.0)"
    pr = re.search(r"PRIOR_SOURCE_WEIGHTS\['kaggle_nutrient'\]\s*=\s*([0-9.]+)", src)
    assert pr and float(pr.group(1)) < float(sw.group(1)), (
        "PRIOR_SOURCE_WEIGHTS['kaggle_nutrient'] must be < its sampler weight"
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


def test_frontier_capacity_probe_is_diagnostic_and_off_by_default():
    """The Frontier decision must be evidence-driven: a frozen linear probe on a
    BIGGER DINOv2 over the same splits, compared to the ViT-S probe on the thin
    classes. It must exist, default OFF (so normal runs aren't slowed), and stay
    diagnostic (it must never become the shipped backbone)."""
    src = _notebook_source()
    assert re.search(r"FRONTIER_PROBE\s*=\s*False", src), (
        "Frontier capacity probe must exist and default to False"
    )
    assert "FRONTIER_PROBE_BACKBONE" in src and "dinov2_vitl14" in src, (
        "Frontier probe must run a larger DINOv2 backbone"
    )
    assert "mean thin-class gain" in src and "FRONTIER_THIN_GAIN_GATE" in src, (
        "Frontier probe must weigh the THIN-class recall gain against a gate"
    )


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
