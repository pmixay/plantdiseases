"""Shared HTTP identity and etiquette for Wikimedia (and iNaturalist) fetches.

Wikimedia's robot policy (https://w.wiki/4wJS) asks every automated client to
send a descriptive ``User-Agent`` with a way to reach the operator, to keep at
most two media downloads in flight, and to honour ``Retry-After`` on HTTP 429.
Both the species-info lookups and the photo proxy go through here so they
present one consistent identity.
"""
from __future__ import annotations

import email.utils
import time

try:
    from .config import get_str
except ImportError:  # flat layout (uvicorn run from server/)
    from config import get_str


def user_agent(purpose: str) -> str:
    """``PlantDiseases/<v> (plant care app; <purpose>; <contact>)``.

    The contact is deployment config (``SPECIES_INFO_CONTACT``: a URL or
    e-mail), never hard-coded in the repository. Without it the agent is still
    descriptive, but Wikimedia throttles contact-less clients first.
    """
    contact = (get_str("SPECIES_INFO_CONTACT", "") or "").strip()
    base = f"PlantDiseases/1.2 (plant care app; {purpose}"
    return f"{base}; {contact})" if contact else f"{base})"


def retry_after_seconds(value: str | None, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or an
    HTTP date); ``None`` when absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))
