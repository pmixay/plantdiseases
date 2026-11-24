"""PlantNet species identification: the upstream call, its result cache and counters.

Kept apart from ``ai_services`` (which holds what the OpenRouter-backed features
share) because PlantNet is a different vendor with its own budget authority
(``plantnet_budget``), key and metrics. Callers reach it as ``plantnet.name`` at
call time, so a test patches the name on this module.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
import threading
import uuid
from typing import Any

try:
    from . import ai_services
    from .config import get_float, get_int, get_secret, get_str
    from .plantnet_budget import (
        PlantNetBudgetDecision,
        PlantNetBudgetUnavailable,
        plantnet_budget_client,
    )
except ImportError:
    import ai_services
    from config import get_float, get_int, get_secret, get_str
    from plantnet_budget import (
        PlantNetBudgetDecision,
        PlantNetBudgetUnavailable,
        plantnet_budget_client,
    )

logger = logging.getLogger(__name__)

# ── Configuration (config.json + env fallback) ──────────────────────


PLANTNET_API_KEY = get_secret("PLANTNET_API_KEY")
PLANTNET_PROJECT = get_str("PLANTNET_PROJECT", "all")
# The free tier is 500 identifications a day for the whole deploy. One client
# cannot drain it: main.py gives each IP its own daily PlantNet allowance
# (PLANTNET_DAILY_QUOTA_PER_IP) on top of the per-IP rate limiter.
PLANTNET_TIMEOUT = get_float("PLANTNET_TIMEOUT", 10.0)
# One bounded retry: a single 8s attempt was the most frequent recurring
# failure, so we retry once on a transient network fault (connect/read
# timeout or connection error). Auth (401) and rate-limit (429) are terminal
# and never retried; any other error still degrades gracefully.
PLANTNET_ATTEMPTS = get_int("PLANTNET_ATTEMPTS", 2)
PLANTNET_CACHE_SIZE = get_int("PLANTNET_CACHE_SIZE", 256)
# Stable content (a species ID for a fixed image) — 7-day TTL so a PlantNet
# model update / reclassification eventually refreshes instead of forever.
PLANTNET_CACHE_TTL_SECONDS = get_int("PLANTNET_CACHE_TTL_SECONDS", 7 * 24 * 3600)


def plantnet_configured() -> bool:
    """Whether species identification can make a paid upstream request."""
    return bool(PLANTNET_API_KEY and plantnet_budget_client is not None)


_PLANTNET_CACHE = ai_services._LRU(PLANTNET_CACHE_SIZE, PLANTNET_CACHE_TTL_SECONDS)
_PLANTNET_METRICS_LOCK = threading.Lock()
_PLANTNET_METRICS = {
    "cache_hits_total": 0,
    "upstream_attempts_total": 0,
    "upstream_retries_total": 0,
    "budget_denials_total": 0,
    "budget_unavailable_total": 0,
    "reserved_cost_micros_total": 0,
    "daily_cost_exposure_micros": 0,
}


def plantnet_metrics_snapshot() -> dict[str, int]:
    """Return aggregate counters only; never request, image, or account data."""
    with _PLANTNET_METRICS_LOCK:
        return dict(_PLANTNET_METRICS)


def _record_plantnet_metric(name: str, amount: int = 1) -> None:
    with _PLANTNET_METRICS_LOCK:
        _PLANTNET_METRICS[name] += amount


def _record_plantnet_budget(decision: PlantNetBudgetDecision) -> None:
    with _PLANTNET_METRICS_LOCK:
        _PLANTNET_METRICS["daily_cost_exposure_micros"] = (
            decision.cost_exposure_micros
        )
        if decision.allowed:
            _PLANTNET_METRICS["reserved_cost_micros_total"] += (
                decision.attempt_cost_micros
            )
        else:
            _PLANTNET_METRICS["budget_denials_total"] += 1


# ── PlantNet species identification ─────────────────────────────────


def _image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def _redact_key(msg: str) -> str:
    """Strip the PlantNet api-key from a log/exception string. ``requests``
    embeds the full request URL — including the ``?api-key=<secret>`` query
    param — in HTTPError/Timeout/ConnectionError messages, so logging one
    verbatim leaks the key into journald on every non-2xx (e.g. the routine
    404s on non-plant images)."""
    msg = re.sub(r"(api-key=)[^&\s]+", r"\1***", msg)
    if PLANTNET_API_KEY:
        msg = msg.replace(PLANTNET_API_KEY, "***")
    return msg


def _plantnet_cache_key(image_bytes: bytes, *, organ: str, lang: str) -> str:
    return f"{_image_hash(image_bytes)}:{organ}:{lang}"


def plantnet_cache_lookup(
    image_bytes: bytes,
    *,
    organ: str = "auto",
    lang: str = "en",
) -> dict | None:
    """Return a safe cached copy before any user/vendor allowance is spent."""
    normalized_lang = (lang or "en").strip().lower()[:5] or "en"
    cached = _PLANTNET_CACHE.get(
        _plantnet_cache_key(image_bytes, organ=organ, lang=normalized_lang)
    )
    if cached is None:
        return None
    _record_plantnet_metric("cache_hits_total")
    return {
        **copy.deepcopy(cached),
        "cached": True,
        "_upstream_attempted": False,
    }


def identify_species(
    image_bytes: bytes, *, organ: str = "auto", lang: str = "en",
) -> dict:
    """Identify the plant species via PlantNet.

    Returns a dict with up to 5 candidate species. When the API key is
    missing we return ``{"available": False, "results": []}`` so the
    caller can render a graceful degraded state.

    ``lang`` is forwarded to PlantNet's ``lang`` query param so the
    returned ``common_names`` are localised — "Каучуковое дерево"
    in Russian instead of "Rubber tree". The English/Latin
    scientific name is unchanged.
    """
    if not PLANTNET_API_KEY:
        return {
            "available": False,
            "results": [],
            "source": "no_api_key",
            "_upstream_attempted": False,
        }

    # Normalise so we don't cache "en" and "EN" as separate entries.
    lang = (lang or "en").strip().lower()[:5] or "en"

    cache_key = _plantnet_cache_key(image_bytes, organ=organ, lang=lang)
    cached = plantnet_cache_lookup(image_bytes, organ=organ, lang=lang)
    if cached is not None:
        # Deep-copy so the caller can mutate the returned ``results``
        # list / ``common_names`` sub-list without poisoning the LRU
        # entry for the next reader (``{**cached, ...}`` is a shallow
        # copy and the nested list-of-dicts is still aliased). Set the private
        # marker here as well as in ``plantnet_cache_lookup``: an endpoint can
        # miss its pre-allowance probe and then race with another request that
        # fills this inner cache before the allowance-backed call begins.
        return {**cached, "_upstream_attempted": False}

    upstream_attempted = False
    try:
        # Lazy import — only the identify path needs requests.
        import requests

        # Pass the API key via the query *params* argument rather than
        # interpolating it into the URL string: a raised requests
        # exception or any log of ``url`` would otherwise leak the secret
        # into access logs / error traces.
        url = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}"
        params = {"api-key": PLANTNET_API_KEY, "lang": lang}
        files = {"images": ("plant.jpg", image_bytes, "image/jpeg")}
        # organs="auto" lets PlantNet detect the actual organ (flower/fruit/
        # leaf/whole-plant) — far more accurate than forcing "leaf" on a photo
        # that is really a bloom or whole plant (PlantNet docs' own guidance).
        data = {"organs": organ}
        # Retry only on a transient network fault (connect/read timeout or
        # connection error). Auth (401) / rate-limit (429) short-circuit
        # inside the loop and never retry; any other error breaks out to the
        # graceful degraded result below.
        attempts = max(1, PLANTNET_ATTEMPTS)
        payload: Any = None
        for attempt in range(attempts):
            if plantnet_budget_client is None:
                _record_plantnet_metric("budget_unavailable_total")
                return {
                    "available": False,
                    "results": [],
                    "source": "budget_unavailable",
                    "_upstream_attempted": upstream_attempted,
                }
            try:
                budget = plantnet_budget_client.reserve(request_id=str(uuid.uuid4()))
            except PlantNetBudgetUnavailable:
                _record_plantnet_metric("budget_unavailable_total")
                return {
                    "available": False,
                    "results": [],
                    "source": "budget_unavailable",
                    "_upstream_attempted": upstream_attempted,
                }
            if budget.replayed:
                # This process never persists/resumes an upstream attempt ID,
                # so a replay can only be a collision or broken caller. Do not
                # let one previously reserved unit authorize a second request.
                _record_plantnet_metric("budget_unavailable_total")
                return {
                    "available": False,
                    "results": [],
                    "source": "budget_unavailable",
                    "_upstream_attempted": upstream_attempted,
                }
            _record_plantnet_budget(budget)
            if not budget.allowed:
                return {
                    "available": False,
                    "results": [],
                    "source": "budget_exhausted",
                    "_upstream_attempted": upstream_attempted,
                }
            if attempt > 0:
                _record_plantnet_metric("upstream_retries_total")
            _record_plantnet_metric("upstream_attempts_total")
            try:
                upstream_attempted = True
                r = requests.post(
                    url, params=params, files=files, data=data,
                    timeout=PLANTNET_TIMEOUT,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt + 1 < attempts:
                    logger.info(
                        "PlantNet transient failure on attempt %d/%d; retrying",
                        attempt + 1, attempts,
                    )
                    continue
                raise
            if r.status_code == 401:
                logger.error("PlantNet auth failed - check PLANTNET_API_KEY")
                return {
                    "available": False,
                    "results": [],
                    "source": "auth_failed",
                    "_upstream_attempted": True,
                }
            if r.status_code == 429:
                logger.warning("PlantNet rate-limited; surfacing graceful degraded result")
                return {
                    "available": False,
                    "results": [],
                    "source": "rate_limited",
                    "_upstream_attempted": True,
                }
            r.raise_for_status()
            payload = r.json()
            break
    except Exception:  # noqa: BLE001 — provider details may contain credentials
        logger.warning("PlantNet identify failed")
        return {
            "available": False,
            "results": [],
            "source": "error",
            "_upstream_attempted": upstream_attempted,
        }

    results: list[dict] = []
    for entry in payload.get("results", [])[:5]:
        species = (entry.get("species") or {})
        score = float(entry.get("score") or 0.0)
        results.append({
            "scientific_name": species.get("scientificNameWithoutAuthor"),
            "common_names": species.get("commonNames") or [],
            "family": (species.get("family") or {}).get("scientificNameWithoutAuthor"),
            "genus": (species.get("genus") or {}).get("scientificNameWithoutAuthor"),
            "score": round(score, 4),
        })

    response = {
        "available": True,
        "results": results,
        "remaining_requests": payload.get("remainingIdentificationRequests"),
        "source": "plantnet",
    }
    _PLANTNET_CACHE.put(cache_key, response)
    return {**response, "_upstream_attempted": True}
