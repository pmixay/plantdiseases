"""Disease progression between scans: one pair (``compare_progression``)
and a history sparkline with a worsening alert (``compare_progression_history``).
Deterministic, no model or network."""

from __future__ import annotations

# ── Disease progression analysis ────────────────────────────────────


_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


def compare_progression(*, current: dict, previous: dict | None) -> dict:
    """Pure-function progression diff between two scans.

    ``current`` and ``previous`` are dicts shaped like the
    ``/api/analyze`` response. ``previous`` may be ``None`` (first
    scan), in which case ``trend`` is ``"baseline"``.
    """
    if not previous:
        return {
            "trend": "baseline",
            "delta_confidence": 0.0,
            "delta_severity": 0,
            "class_changed": False,
            "previous_class": None,
            "current_class": current.get("class_name") or current.get("disease_name"),
        }

    cur_cls = current.get("class_name") or current.get("disease_name")
    prv_cls = previous.get("class_name") or previous.get("disease_name")
    cur_conf = float(current.get("confidence") or 0.0)
    prv_conf = float(previous.get("confidence") or 0.0)
    cur_sev = _SEVERITY_ORDER.get(current.get("severity", "low"), 0)
    prv_sev = _SEVERITY_ORDER.get(previous.get("severity", "low"), 0)

    delta_sev = cur_sev - prv_sev
    delta_conf = round(cur_conf - prv_conf, 4)

    # Trend rules:
    #  - moved from healthy/NAP into a disease class → "worsening"
    #  - moved from a disease into healthy/NAP → "improving"
    #  - same class, severity increased → "worsening"
    #  - same class, severity decreased → "improving"
    #  - severity unchanged AND |delta_conf| < 0.10 → "stable"
    #  - otherwise track the dominant signal
    cur_is_disease = cur_cls not in ("healthy", "not_a_plant", None)
    prv_is_disease = prv_cls not in ("healthy", "not_a_plant", None)

    if not prv_is_disease and cur_is_disease:
        trend = "worsening"
    elif prv_is_disease and not cur_is_disease:
        trend = "improving"
    elif delta_sev > 0:
        trend = "worsening"
    elif delta_sev < 0:
        trend = "improving"
    elif abs(delta_conf) < 0.10:
        trend = "stable"
    else:
        trend = "worsening" if delta_conf > 0 else "improving"

    return {
        "trend": trend,
        "delta_confidence": delta_conf,
        "delta_severity": delta_sev,
        "class_changed": cur_cls != prv_cls,
        "previous_class": prv_cls,
        "current_class": cur_cls,
    }


# ── Historical progression (5-point sparkline) ──────────────────────


_SPARKLINE_POINTS = 5


def _severity_score(entry: dict) -> float:
    """Map an analyse-shaped dict into a 0..1 severity score.

    Combines the categorical severity (low/medium/high) with the model
    confidence so two "high" verdicts at 0.6 and 0.95 don't render as
    identical sparkline points. Healthy / NAP collapse to 0.
    """
    cls = entry.get("class_name") or entry.get("disease_name")
    if cls in ("healthy", "not_a_plant", None, ""):
        return 0.0
    sev = _SEVERITY_ORDER.get(entry.get("severity", "low"), 0)
    conf = float(entry.get("confidence") or 0.0)
    # 0 → 0.10, 1 → 0.40, 2 → 0.70 + (0..0.30) from confidence.
    base = 0.10 + 0.30 * sev
    return round(min(1.0, base + 0.30 * max(0.0, min(1.0, conf))), 4)


def _resample_to_points(values: list[float], points: int) -> list[float]:
    """Resample ``values`` to exactly ``points`` samples via piecewise
    linear interpolation. ``values`` is assumed to be time-ordered
    (oldest → newest). Single-element inputs broadcast.

    Pure stdlib; no numpy dependency."""
    n = len(values)
    if n == 0 or points <= 0:
        return []
    if n == 1:
        return [round(values[0], 4)] * points
    if points == 1:
        return [round(values[-1], 4)]
    out: list[float] = []
    last_idx = n - 1
    for i in range(points):
        # Position on the source axis in [0, n-1].
        t = i * last_idx / (points - 1)
        lo = int(t)
        hi = min(lo + 1, last_idx)
        frac = t - lo
        out.append(round(values[lo] * (1.0 - frac) + values[hi] * frac, 4))
    return out


def _linear_trend_slope(values: list[float]) -> float:
    """Least-squares slope of ``values`` against their indices. Used to
    pick worsening / improving / stable when we have ≥ 3 points."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den <= 0:
        return 0.0
    return num / den


def compare_progression_history(history: list[dict]) -> dict:
    """Build a 5-point severity sparkline from a time-ordered scan history.

    ``history`` is a list of analyse-shaped dicts, oldest first. Returns:

      - ``sparkline``         : list[float], length 5, severity ∈ [0, 1]
      - ``trend``             : "improving" / "stable" / "worsening" / "baseline"
      - ``history_length``    : int, raw input length
      - ``current_class``     : class of the newest scan (or null)
      - ``previous_class``    : class of the second-newest scan (or null)
      - ``delta_confidence``  : float, newest.conf − previous.conf
      - ``delta_severity``    : int, newest.sev − previous.sev
      - ``class_changed``     : bool
      - ``points``            : the per-scan severity scores before resampling

    On an empty history the response is ``trend="baseline"`` with a
    zero-padded sparkline, so the client can render an "awaiting data"
    state without special-casing.
    """
    if not isinstance(history, list):
        history = []
    cleaned = [h for h in history if isinstance(h, dict)]
    points_raw = [_severity_score(h) for h in cleaned]
    sparkline = _resample_to_points(points_raw, _SPARKLINE_POINTS) \
        if points_raw else [0.0] * _SPARKLINE_POINTS

    if not cleaned:
        return {
            "sparkline": sparkline,
            "trend": "baseline",
            "history_length": 0,
            "current_class": None,
            "previous_class": None,
            "delta_confidence": 0.0,
            "delta_severity": 0,
            "class_changed": False,
            "points": [],
        }

    current = cleaned[-1]
    previous = cleaned[-2] if len(cleaned) >= 2 else None
    pair = compare_progression(current=current, previous=previous)
    # Re-evaluate the trend using the linear slope across the full
    # history when we have ≥ 3 points — the pairwise verdict above
    # is just the newest-vs-previous diff, which can flap.
    if len(points_raw) >= 3:
        slope = _linear_trend_slope(points_raw)
        if abs(slope) < 0.02:
            pair["trend"] = "stable"
        elif slope > 0:
            pair["trend"] = "worsening"
        else:
            pair["trend"] = "improving"

    # Alert flag — fires when the LAST TWO points in the severity
    # series both rose vs the point before. Two-in-a-row guards
    # against a single noisy scan flipping the flag. The plant-
    # detail screen treats this as a hint to push a "your plant
    # is getting worse — take a fresh scan?" notification.
    alert = False
    alert_reason: str | None = None
    if len(points_raw) >= 3:
        last3 = points_raw[-3:]
        if last3[1] > last3[0] + 1e-3 and last3[2] > last3[1] + 1e-3:
            alert = True
            alert_reason = "two_step_worsening"

    return {
        "sparkline": sparkline,
        "trend": pair["trend"],
        "history_length": len(cleaned),
        "current_class": pair["current_class"],
        "previous_class": pair["previous_class"],
        "delta_confidence": pair["delta_confidence"],
        "delta_severity": pair["delta_severity"],
        "class_changed": pair["class_changed"],
        "points": [round(p, 4) for p in points_raw],
        "alert": alert,
        "alert_reason": alert_reason,
    }
