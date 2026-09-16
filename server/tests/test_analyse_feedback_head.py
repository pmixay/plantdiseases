"""``tools/analyse_feedback.py`` must count corrections against the real head."""

from __future__ import annotations

import classifier
from tools import analyse_feedback as af


def test_in_head_classes_derive_from_the_classifier_head():
    assert af.IN_HEAD_CLASSES == frozenset(classifier.DEFAULT_CLASS_NAMES)
    # The three classes the old hard-coded copy dropped.
    for name in ("leaf_scorch_underwatering", "nutrient_deficiency", "pests"):
        assert name in af.IN_HEAD_CLASSES
