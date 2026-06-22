# PlantVision — canonical analysis boilerplate
# ---------------------------------------------
# Drop this at the top of any scratch / eval / analysis notebook or script so
# the numbers you produce are directly comparable to the ship gate. It mirrors
# `server/train_notebook.ipynb` §1 (SEED, taxonomy) and
# `server/tools/eval_bootstrap.py` (disease-macro-F1 definition).
#
# `nbtool.py scaffold OUT.ipynb` emits a notebook pre-seeded with this.

import json
import random

import numpy as np
from sklearn.metrics import f1_score, recall_score, confusion_matrix

# ── Reproducibility — same seed as the training notebook ─────────
SEED = 0
random.seed(SEED)
np.random.seed(SEED)

# ── Taxonomy — 9 trainable classes, alphabetical ────────────────
# This order == sorted(ImageFolder.classes); the model's logit index i maps to
# CLASS_NAMES[i]. Keep in sync with models/classes.json on a real bundle (the
# shipped contract wins over this hard-coded copy if they ever differ).
CLASS_NAMES = [
    "gray_mold_botrytis",        # 0
    "healthy",                   # 1
    "leaf_scorch_underwatering", # 2
    "leaf_spot",                 # 3
    "not_a_plant",               # 4
    "nutrient_deficiency",       # 5
    "pests",                     # 6
    "powdery_mildew",            # 7
    "viral_disease",             # 8
]

# "Disease" excludes the two non-disease classes. The ship gate scores
# macro-F1 over disease classes only — healthy/NaP are not diseases and would
# otherwise inflate the metric.
NON_DISEASE = {"healthy", "not_a_plant"}
DISEASE_IDX = [i for i, c in enumerate(CLASS_NAMES) if c not in NON_DISEASE]


def disease_macro_f1(y_true, y_pred) -> float:
    """Macro-F1 over DISEASE classes only — the primary ship-gate metric.

    Matches tools/eval_bootstrap.py:disease_macro_f1 so a number here is
    comparable to the gate (MIN_DISEASE_F1 = 0.70 on val_realworld).
    """
    return float(
        f1_score(y_true, y_pred, labels=DISEASE_IDX, average="macro", zero_division=0)
    )


def per_class_recall(y_true, y_pred) -> dict[str, float]:
    """Recall per class as a {name: recall} dict. A disease class at recall=0
    is an automatic ship-gate failure, so this is the first thing to inspect."""
    rec = recall_score(
        y_true, y_pred, labels=list(range(len(CLASS_NAMES))),
        average=None, zero_division=0,
    )
    return {CLASS_NAMES[i]: float(rec[i]) for i in range(len(CLASS_NAMES))}


# For confidence intervals and a *paired* A/B (never trust a bare point-estimate
# F1 gap — the sampling error at n≈2k is wide), prefer the repo's own estimator:
#
#     import sys; sys.path.insert(0, "server")
#     from tools.eval_bootstrap import (
#         bootstrap_macro_f1_ci,      # single-model CI
#         paired_bootstrap,           # A vs B on the SAME test set
#         bootstrap_per_class_recall_ci,
#     )
