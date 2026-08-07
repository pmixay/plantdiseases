from __future__ import annotations

import pytest
from PIL import Image

from beta_runtime import allow_synthetic_pipeline
from classifier import DEFAULT_CLASS_NAMES
from synthetic_pipeline import SYNTHETIC_CLASS_NAMES, SyntheticPipeline


@pytest.mark.parametrize("environment", ["", "development", "staging", "production"])
def test_synthetic_pipeline_disabled_is_always_safe(environment: str):
    assert allow_synthetic_pipeline(environment=environment, requested=False) is False


@pytest.mark.parametrize("environment", ["", "development", "staging", "production"])
def test_synthetic_pipeline_refuses_every_non_test_environment(environment: str):
    with pytest.raises(RuntimeError, match="only when ENVIRONMENT=test"):
        allow_synthetic_pipeline(environment=environment, requested=True)


def test_synthetic_pipeline_requires_explicit_test_environment():
    assert allow_synthetic_pipeline(environment=" test ", requested=True) is True


def test_synthetic_pipeline_matches_taxonomy_and_response_shape():
    assert SYNTHETIC_CLASS_NAMES == DEFAULT_CLASS_NAMES
    pipeline = SyntheticPipeline()
    result = pipeline.analyze(Image.new("RGB", (64, 64), (40, 150, 60)))

    assert result["class_name"] == "healthy"
    assert result["warnings"] == ["synthetic_test_result"]
    assert set(result["all_probs"]) == set(DEFAULT_CLASS_NAMES)
    assert abs(sum(result["all_probs"].values()) - 1.0) < 0.00001
    assert result["detection"] == {
        "is_diseased": False,
        "detector_confidence": 0.0,
        "regions": [],
        "primary_region": None,
    }
