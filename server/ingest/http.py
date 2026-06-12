"""HTTP helpers with exponential backoff (ported from train_notebook.ipynb §2).

Bounded retries on rate-limit / transient server errors; per-call failures
return ``None`` / raise so the caller can fail-soft and continue.
"""

from __future__ import annotations

import time

import requests

UA = {"User-Agent": "PlantVision-Labeling/1.0 (research; server ingestion)"}
_RETRY_STATUS = (429, 500, 502, 503, 504)
_SESSION = requests.Session()


def http_get_json(url, params=None, timeout=30, retries=4):
    """GET JSON with backoff. Raises on final failure."""
    last = None
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code in _RETRY_STATUS:
                raise requests.HTTPError(f"status {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(2 ** attempt, 16))
    raise last  # type: ignore[misc]


def http_get_bytes(url, timeout=20, retries=3):
    """GET raw bytes. Returns ``None`` on failure (soft skip)."""
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, headers=UA, timeout=timeout)
            if r.status_code in _RETRY_STATUS:
                raise requests.HTTPError(f"status {r.status_code}")
            r.raise_for_status()
            return r.content
        except Exception:  # noqa: BLE001
            time.sleep(min(2 ** attempt, 8))
    return None
