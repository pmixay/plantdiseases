"""Production-scan routing (server/labeling_auto.route_scan)."""

from __future__ import annotations

from labeling_auto import route_scan


def test_route_scan_singleton_set_auto_yes():
    assert route_scan("healthy", 0.95, ["healthy"]) == "auto_yes"


def test_route_scan_multiclass_set_pending():
    assert route_scan("leaf_spot", 0.95, ["leaf_spot", "viral_disease"]) == "pending"


def test_route_scan_no_set_falls_back_to_confidence():
    assert route_scan("pests", 0.9, None) == "auto_yes"
    assert route_scan("pests", 0.5, []) == "pending"
    assert route_scan("pests", None, None) == "pending"


def test_route_scan_conformal_disabled_uses_confidence():
    # set says singleton but conformal is distrusted → confidence decides
    assert route_scan("pests", 0.5, ["pests"], trust_conformal=False) == "pending"
    assert route_scan("pests", 0.9, ["pests"], trust_conformal=False) == "auto_yes"
