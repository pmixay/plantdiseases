"""Feedback must count corrections against the real classifier head."""

from __future__ import annotations

import classifier


def test_in_head_classes_derive_from_the_classifier_head():
    import feedback_labels
    in_head = feedback_labels.FEEDBACK_IN_HEAD_CLASSES
    assert in_head == frozenset(classifier.DEFAULT_CLASS_NAMES)
    # The three classes a hard-coded six-class copy once dropped, which
    # silently mis-binned their corrections as out-of-head.
    for name in ("leaf_scorch_underwatering", "nutrient_deficiency", "pests"):
        assert name in in_head
