"""compare_progression: the pairwise scan-to-scan trend (the history form is covered
through the route in test_progression_history.py)."""


def test_progression_baseline_when_no_previous():
    import progression

    out = progression.compare_progression(
        current={"class_name": "leaf_spot", "confidence": 0.7,
                 "severity": "medium"},
        previous=None,
    )
    assert out["trend"] == "baseline"
    assert out["delta_severity"] == 0


def test_progression_worsening_on_severity_step_up():
    import progression

    out = progression.compare_progression(
        current={"class_name": "leaf_spot", "confidence": 0.85,
                 "severity": "high"},
        previous={"class_name": "leaf_spot", "confidence": 0.65,
                  "severity": "medium"},
    )
    assert out["trend"] == "worsening"
    assert out["delta_severity"] == 1


def test_progression_improving_disease_to_healthy():
    import progression

    out = progression.compare_progression(
        current={"class_name": "healthy", "confidence": 0.92, "severity": "low"},
        previous={"class_name": "viral_disease", "confidence": 0.7,
                  "severity": "medium"},
    )
    assert out["trend"] == "improving"
    assert out["class_changed"] is True
