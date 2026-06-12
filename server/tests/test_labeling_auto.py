"""Model-trust auto-labeling routing (server/labeling_auto.py)."""

from __future__ import annotations

import io

from PIL import Image

from labeling_auto import classify_candidate


class _StubPipeline:
    """Returns a crafted analyze() result so routing is deterministic."""

    def __init__(self, class_name, confidence, conformal_classes):
        self._r = {
            "class_name": class_name,
            "confidence": confidence,
            "prediction_set": ({"classes": conformal_classes}
                               if conformal_classes is not None else None),
            "all_probs": {},
        }

    def analyze(self, img):
        return self._r


def _img():
    return Image.open(io.BytesIO(
        __import__("base64").b64decode(
            b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
    )).convert("RGB")


def test_conformal_singleton_agree_auto_yes():
    p = _StubPipeline("nutrient_deficiency", 0.9, ["nutrient_deficiency"])
    status, pred, conf, mset = classify_candidate(p, _img(), "nutrient_deficiency")
    assert status == "auto_yes" and pred == "nutrient_deficiency" and mset == ["nutrient_deficiency"]


def test_conformal_multiset_is_confusing_pending():
    p = _StubPipeline("leaf_spot", 0.5, ["leaf_spot", "nutrient_deficiency"])
    status, *_ = classify_candidate(p, _img(), "nutrient_deficiency")
    assert status == "pending"


def test_proposed_excluded_confident_auto_no():
    p = _StubPipeline("leaf_spot", 0.95, ["leaf_spot"])
    status, *_ = classify_candidate(p, _img(), "nutrient_deficiency")
    assert status == "auto_no"


def test_proposed_excluded_unsure_pending():
    p = _StubPipeline("leaf_spot", 0.6, ["leaf_spot"])
    status, *_ = classify_candidate(p, _img(), "nutrient_deficiency")
    assert status == "pending"


def test_not_a_plant_auto_no():
    p = _StubPipeline("not_a_plant", 0.99, None)
    status, *_ = classify_candidate(p, _img(), "gray_mold_botrytis")
    assert status == "auto_no"


def test_fallback_no_conformal_agree():
    p = _StubPipeline("gray_mold_botrytis", 0.9, None)
    status, *_ = classify_candidate(p, _img(), "gray_mold_botrytis", trust_conformal=False)
    assert status == "auto_yes"


def test_fallback_low_conf_pending():
    p = _StubPipeline("gray_mold_botrytis", 0.6, None)
    status, *_ = classify_candidate(p, _img(), "gray_mold_botrytis", trust_conformal=False)
    assert status == "pending"


def test_analyze_error_routes_to_human():
    class _Boom:
        def analyze(self, img):
            raise RuntimeError("model down")
    status, pred, conf, mset = classify_candidate(_Boom(), _img(), "leaf_spot")
    assert status == "pending" and pred is None and mset == []
