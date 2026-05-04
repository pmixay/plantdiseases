"""
PlantVision — AI / smart-API services.

Houses the supporting endpoints layered on top of the disease classifier:

* **PlantNet identify** — ``identify_species(image_bytes)`` calls the
  PlantNet ``/v2/identify`` endpoint when a ``PLANTNET_API_KEY`` is set,
  with a server-side LRU cache keyed on a SHA-256 of the image so we
  don't burn quota re-identifying the same photo within a deploy.

* **Anthropic AI Plant Doctor** — ``chat_reply()`` runs a Claude Haiku
  4.5 conversation with prompt caching on the (long, stable) system
  context describing the disease taxonomy and care advice. Per-session
  conversation buffer + per-session budget cap (token + message) so a
  rogue client can't drain the API budget.

* **Care plan** — ``generate_care_plan()`` deterministic rules-based
  plan, optionally polished by Claude when an API key is configured.
  Always returns a usable plan even with no key set.

* **Progression analyser** — ``compare_progression()`` pure function
  comparing the current scan to a prior scan dict. Used by
  ``/api/progression`` so the Android client can render a "getting
  worse / improving / stable" trend.

All external calls are bounded by environment-driven budgets and
fail gracefully — losing PlantNet or Anthropic must never break the
core ``/api/analyze`` flow.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from config import get_float, get_int, get_str

logger = logging.getLogger(__name__)

# ── Configuration (config.json + env fallback) ──────────────────────

PLANTNET_API_KEY = get_str("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = get_str("PLANTNET_PROJECT", "all")
# Free tier is 500 / day. We expose 5/min as a soft client cap on top
# of the existing per-IP rate limiter to keep one deploy from burning
# the daily budget on a single bad actor.
PLANTNET_TIMEOUT = get_float("PLANTNET_TIMEOUT", 8.0)
PLANTNET_CACHE_SIZE = get_int("PLANTNET_CACHE_SIZE", 256)

# Pluggable chat provider. Supported: "openrouter" (default), "anthropic".
AI_PROVIDER = get_str("AI_PROVIDER", "openrouter").strip().lower()

# OpenRouter (default; routes to openai/gpt-oss-120b unless overridden)
OPENROUTER_API_KEY = get_str("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = get_str("OPENROUTER_MODEL", "openai/gpt-oss-120b").strip()
OPENROUTER_BASE_URL = get_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

ANTHROPIC_API_KEY = get_str("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = get_str("ANTHROPIC_MODEL", "claude-haiku-4-5")
# Hard caps so a runaway client can't blow the budget. Tune via config
# in production.
CHAT_MAX_TOKENS = get_int("CHAT_MAX_TOKENS", 512)
CHAT_SESSION_TURN_CAP = get_int("CHAT_SESSION_TURN_CAP", 20)
CHAT_SESSION_TOKEN_CAP = get_int("CHAT_SESSION_TOKEN_CAP", 8000)
CHAT_SESSION_TTL_SEC = get_int("CHAT_SESSION_TTL_SEC", 3600)


def _active_chat_model() -> str:
    if AI_PROVIDER == "openrouter":
        return OPENROUTER_MODEL
    return ANTHROPIC_MODEL


def _chat_api_key_present() -> bool:
    if AI_PROVIDER == "openrouter":
        return bool(OPENROUTER_API_KEY)
    return bool(ANTHROPIC_API_KEY)

# Single source of truth for the disease taxonomy used in the LLM
# system prompt. Imported inside the chat function to avoid a circular
# import on module load.


# ── Generic LRU helper ──────────────────────────────────────────────


class _LRU:
    """Tiny thread-safe LRU. Keeps the dependency surface to stdlib only."""

    def __init__(self, maxsize: int):
        self.maxsize = max(1, int(maxsize))
        self._d: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                self._d[key] = value
                return
            self._d[key] = value
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def __len__(self) -> int:  # for /api/metrics
        with self._lock:
            return len(self._d)


_PLANTNET_CACHE = _LRU(PLANTNET_CACHE_SIZE)


# ── PlantNet species identification ─────────────────────────────────


def _image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def identify_species(image_bytes: bytes, *, organ: str = "leaf") -> dict:
    """Identify the plant species via PlantNet.

    Returns a dict with up to 5 candidate species. When the API key is
    missing we return ``{"available": False, "results": []}`` so the
    caller can render a graceful degraded state.
    """
    if not PLANTNET_API_KEY:
        return {"available": False, "results": [], "source": "no_api_key"}

    cache_key = f"{_image_hash(image_bytes)}:{organ}"
    cached = _PLANTNET_CACHE.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    try:
        # Lazy import — only the identify path needs requests.
        import requests  # noqa: WPS433

        url = (
            f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}"
            f"?api-key={PLANTNET_API_KEY}"
        )
        files = {"images": ("plant.jpg", image_bytes, "image/jpeg")}
        data = {"organs": organ}
        r = requests.post(url, files=files, data=data, timeout=PLANTNET_TIMEOUT)
        if r.status_code == 401:
            logger.error("PlantNet auth failed — check PLANTNET_API_KEY")
            return {"available": False, "results": [], "source": "auth_failed"}
        if r.status_code == 429:
            logger.warning("PlantNet rate-limited; surfacing graceful degraded result")
            return {"available": False, "results": [], "source": "rate_limited"}
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001 — never propagate to client
        logger.warning("PlantNet identify failed: %s", e)
        return {"available": False, "results": [], "source": "error"}

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
    return response


# ── Claude / AI Plant Doctor ────────────────────────────────────────


# Minimal long-form context for the LLM. Stable prefix → benefits from
# Anthropic prompt caching (5-min TTL), so repeat turns within a
# session don't pay for re-tokenising the system prompt.
_DOCTOR_SYSTEM_PROMPT = """You are PlantDiseases' friendly AI Plant Doctor, helping
people care for their houseplants. Your knowledge is grounded in the
PlantVision v2.0 disease taxonomy (11 classes):

- bacterial_leaf_spot: angular brown spots with yellow halo, bacterial
- edema: corky water-blister bumps on leaf undersides; physiological,
  caused by overwatering in cool dim weather
- gray_mold_botrytis: fuzzy grey growth, fungal (Botrytis cinerea)
- healthy: no disease detected
- leaf_scorch_underwatering: crispy brown leaf tips/edges, chronic
  under-watering or salty hard water
- leaf_spot_fungal: umbrella class for anthracnose / cercospora /
  alternaria — dark spots, halos, concentric rings
- not_a_plant: image does not contain a plant
- nutrient_deficiency: covers iron, nitrogen, magnesium, potassium —
  interveinal or whole-leaf yellowing, edge burn
- powdery_mildew: white powdery coating, fungal
- sunburn_or_scorch: dry brown patches on the sun-facing side of
  leaves or directly under harsh grow-lights
- viral_disease: umbrella class for mosaic, leaf curl and ringspot;
  incurable

Style guide:
- Keep replies SHORT (3-6 sentences). Specific, actionable, friendly.
- Match the user's language (English or Russian).
- If the user describes symptoms outside the taxonomy, say so and
  suggest taking a photo or consulting a local nursery.
- Never invent fungicide trade names. Recommend generic active
  ingredients only (e.g. "copper-based fungicide", "neem oil").
- Never claim the AI can diagnose with certainty — defer to the
  classifier output the user already has."""


class _ChatSession:
    __slots__ = (
        "session_id", "messages", "tokens_in", "tokens_out", "turn_count",
        "created_at", "last_active",
    )

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.messages: list[dict] = []   # [{"role": "user"|"assistant", "content": str}]
        self.tokens_in = 0
        self.tokens_out = 0
        self.turn_count = 0
        self.created_at = time.time()
        self.last_active = self.created_at

    def trim_for_context(self, keep_last: int = 12) -> list[dict]:
        """Trim message history for the next request — only send the last N."""
        if len(self.messages) <= keep_last:
            return list(self.messages)
        return self.messages[-keep_last:]


_chat_sessions: dict[str, _ChatSession] = {}
_chat_lock = threading.Lock()
# Server-wide totals for the abuse log / Prometheus.
_chat_totals = {"requests": 0, "rejected": 0, "tokens_in": 0, "tokens_out": 0}


def _call_anthropic(
    system_blocks: list[dict], user_blocks: list[dict],
    *, max_tokens: int | None = None,
) -> tuple[str, int, int]:
    """Run an Anthropic Claude completion. Returns (text, in_tokens, out_tokens)."""
    import anthropic  # noqa: WPS433

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=20.0)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens or CHAT_MAX_TOKENS,
        system=system_blocks,
        messages=user_blocks,
    )
    text = "".join(
        getattr(b, "text", "") for b in (msg.content or [])
        if getattr(b, "type", "") == "text"
    ).strip()
    usage = getattr(msg, "usage", None)
    tin = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    tout = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    return text, tin, tout


def _call_openrouter(
    system_blocks: list[dict], user_blocks: list[dict],
    *, max_tokens: int | None = None,
) -> tuple[str, int, int]:
    """Run an OpenRouter chat completion. Translates Anthropic-shaped
    system_blocks (list of {"type":"text","text":...}) into a single
    OpenAI-style system message, then issues a standard chat.completions
    POST. Returns (text, in_tokens, out_tokens)."""
    import requests  # noqa: WPS433

    system_text = "\n\n".join(
        b.get("text", "") for b in system_blocks if b.get("type", "text") == "text"
    ).strip()

    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    for m in user_blocks:
        # user_blocks already use OpenAI-compatible role/content shape.
        messages.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model": OPENROUTER_MODEL,
        "max_tokens": max_tokens or CHAT_MAX_TOKENS,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # OpenRouter recommends these for attribution but they are optional.
        "HTTP-Referer": "https://github.com/pmixay/plantdiseases",
        "X-Title": "PlantDiseases",
    }
    r = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers=headers, json=payload, timeout=20.0,
    )
    r.raise_for_status()
    data = r.json()
    choices = data.get("choices") or []
    text = ""
    if choices:
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
    usage = data.get("usage") or {}
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    return text, tin, tout


def _gc_chat_sessions(now: float) -> None:
    stale = [
        sid for sid, sess in _chat_sessions.items()
        if now - sess.last_active > CHAT_SESSION_TTL_SEC
    ]
    for sid in stale:
        del _chat_sessions[sid]


def chat_reply(
    *,
    session_id: str,
    user_message: str,
    locale: str = "en",
    diagnosis_context: Optional[dict] = None,
) -> dict:
    """Reply to a single user turn within a session.

    Enforces per-session caps (turn count + token budget) BEFORE making
    the API call so a rogue client can't drain the budget by spamming
    long messages. ``diagnosis_context`` is the optional last analyse
    response so the doctor can ground the reply in actual classifier
    output.
    """
    now = time.time()
    user_message = (user_message or "").strip()
    if not user_message:
        return {"available": False, "reply": "", "reason": "empty_message"}
    if len(user_message) > 2000:
        return {"available": False, "reply": "", "reason": "message_too_long"}

    if not _chat_api_key_present():
        return {
            "available": False,
            "reply": "",
            "reason": "no_api_key",
            "fallback": (
                "AI Plant Doctor is offline (no API key). "
                "Use the diagnosis treatment list for now."
            ),
        }

    with _chat_lock:
        _gc_chat_sessions(now)
        sess = _chat_sessions.get(session_id)
        if sess is None:
            sess = _ChatSession(session_id)
            _chat_sessions[session_id] = sess
        if sess.turn_count >= CHAT_SESSION_TURN_CAP:
            _chat_totals["rejected"] += 1
            return {
                "available": False,
                "reply": "",
                "reason": "session_turn_cap",
                "turn_cap": CHAT_SESSION_TURN_CAP,
            }
        if sess.tokens_in + sess.tokens_out >= CHAT_SESSION_TOKEN_CAP:
            _chat_totals["rejected"] += 1
            return {
                "available": False,
                "reply": "",
                "reason": "session_token_cap",
                "token_cap": CHAT_SESSION_TOKEN_CAP,
            }
        sess.last_active = now
        history = sess.trim_for_context(keep_last=12)

    # Build the system prompt with prompt caching enabled on the long
    # static disease-taxonomy block. Anthropic SDK 0.30+ supports the
    # ``cache_control`` field on system blocks.
    locale_hint = (
        "Reply in Russian." if locale.lower().startswith("ru")
        else "Reply in English."
    )
    system_blocks = [
        {
            "type": "text",
            "text": _DOCTOR_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": locale_hint},
    ]
    if diagnosis_context:
        # Compact JSON so the LLM sees structured signal, not a wall
        # of bilingual text.
        compact = {
            "class": diagnosis_context.get("class_name") or diagnosis_context.get("disease_name"),
            "confidence": diagnosis_context.get("confidence"),
            "severity": diagnosis_context.get("severity"),
            "warnings": diagnosis_context.get("warnings") or [],
        }
        system_blocks.append({
            "type": "text",
            "text": "Latest classifier output:\n" + json.dumps(compact, ensure_ascii=False),
        })

    user_blocks = list(history) + [{"role": "user", "content": user_message}]

    try:
        if AI_PROVIDER == "openrouter":
            reply_text, tin, tout = _call_openrouter(system_blocks, user_blocks)
        else:
            reply_text, tin, tout = _call_anthropic(system_blocks, user_blocks)
    except Exception as e:  # noqa: BLE001
        logger.warning("Chat provider %s failed: %s", AI_PROVIDER, e)
        return {"available": False, "reply": "", "reason": "api_error"}

    with _chat_lock:
        sess.messages.append({"role": "user", "content": user_message})
        sess.messages.append({"role": "assistant", "content": reply_text})
        sess.tokens_in += tin
        sess.tokens_out += tout
        sess.turn_count += 1
        sess.last_active = time.time()
        _chat_totals["requests"] += 1
        _chat_totals["tokens_in"] += tin
        _chat_totals["tokens_out"] += tout
        budget_left_tokens = max(
            0, CHAT_SESSION_TOKEN_CAP - sess.tokens_in - sess.tokens_out,
        )
        budget_left_turns = max(0, CHAT_SESSION_TURN_CAP - sess.turn_count)

    return {
        "available": True,
        "reply": reply_text,
        "model": _active_chat_model(),
        "provider": AI_PROVIDER,
        "usage": {"input_tokens": tin, "output_tokens": tout},
        "session": {
            "session_id": session_id,
            "turns_used": sess.turn_count,
            "turns_remaining": budget_left_turns,
            "tokens_used": sess.tokens_in + sess.tokens_out,
            "tokens_remaining": budget_left_tokens,
        },
    }


def chat_metrics_snapshot() -> dict:
    """Read-only snapshot of cross-session chat counters for /api/metrics."""
    with _chat_lock:
        return {
            "active_sessions": len(_chat_sessions),
            "requests": _chat_totals["requests"],
            "rejected": _chat_totals["rejected"],
            "tokens_in": _chat_totals["tokens_in"],
            "tokens_out": _chat_totals["tokens_out"],
        }


# ── Care plan ───────────────────────────────────────────────────────


# Coarse rule table per (disease, light-need) → adjustments. The light
# tier defaults to "bright_indirect" if the caller doesn't supply one.
# This is intentionally compact — the LLM polish step turns it into a
# friendly schedule when an API key is present.

_BASE_PLANS = {
    "low_light":          {"watering_days": 10, "fertilise_weeks": 6, "humidity": "40-50%"},
    "medium_light":       {"watering_days": 7,  "fertilise_weeks": 4, "humidity": "40-60%"},
    "bright_indirect":    {"watering_days": 5,  "fertilise_weeks": 3, "humidity": "50-70%"},
    "direct_sun":         {"watering_days": 4,  "fertilise_weeks": 3, "humidity": "30-50%"},
}

_DISEASE_ADJUSTMENT = {
    "powdery_mildew":            {"humidity": "30-45%", "note": "Lower humidity; improve airflow."},
    "gray_mold_botrytis":        {"humidity": "30-50%", "note": "Lower humidity; remove debris weekly."},
    "leaf_spot_fungal":          {"note": "Bottom-water only; remove affected leaves; copper fungicide weekly."},
    "bacterial_leaf_spot":       {"note": "No misting; bottom-water; isolate the plant."},
    "viral_disease":             {"note": "Isolate; sanitise tools; the virus has no cure."},
    "edema":                     {"watering_days": "as_needed", "note": "Reduce watering; raise light and airflow."},
    "sunburn_or_scorch":         {"note": "Move out of direct midday sun; bright indirect light only."},
    "leaf_scorch_underwatering": {"watering_days": "as_needed", "note": "Soak-water; flush salts; raise humidity."},
    "nutrient_deficiency":       {"note": "Half-strength balanced NPK; chelated Fe + Mg if interveinal yellowing."},
    "healthy":                   {"note": "Keep your current routine; monitor weekly."},
    "not_a_plant":               {"note": "Try a clearer photo of a single leaf."},
}


def _polish_plan_with_llm(plan: dict, locale: str) -> dict:
    """Optional LLM polish step. Returns the plan with a friendly
    Markdown summary attached, or the input plan unchanged on failure."""
    if not _chat_api_key_present():
        return plan
    locale_hint = (
        "Write the summary in Russian." if locale.lower().startswith("ru")
        else "Write the summary in English."
    )
    system = (
        "You polish a houseplant care plan into 4 short Markdown bullet "
        "points. Keep dosages and intervals exactly as given. "
        + locale_hint
    )
    system_blocks = [{"type": "text", "text": system}]
    user_blocks = [{"role": "user", "content": json.dumps(plan, ensure_ascii=False)}]
    try:
        if AI_PROVIDER == "openrouter":
            polish, _, _ = _call_openrouter(system_blocks, user_blocks, max_tokens=300)
        else:
            polish, _, _ = _call_anthropic(system_blocks, user_blocks, max_tokens=300)
        if polish:
            plan = {**plan, "summary_md": polish}
    except Exception as e:  # noqa: BLE001
        logger.info("Care-plan polish skipped: %s", e)
    return plan


def generate_care_plan(
    *,
    disease: str,
    species: Optional[str] = None,
    light: str = "bright_indirect",
    locale: str = "en",
) -> dict:
    """Build a watering / fertilising / humidity schedule for a plant.

    Always returns a usable plan even with no API key; LLM polish only
    adds a ``summary_md`` field when configured.
    """
    light_key = light if light in _BASE_PLANS else "bright_indirect"
    base = dict(_BASE_PLANS[light_key])
    adjustment = _DISEASE_ADJUSTMENT.get(disease, {"note": "Monitor weekly."})

    plan: dict = {
        "species": species or "unknown",
        "disease": disease,
        "light_tier": light_key,
        "watering_days": adjustment.get("watering_days", base["watering_days"]),
        "fertilise_weeks": adjustment.get("fertilise_weeks", base["fertilise_weeks"]),
        "humidity_target": adjustment.get("humidity", base["humidity"]),
        "key_action": adjustment.get("note", ""),
        "follow_up_days": 14 if disease not in ("healthy", "not_a_plant") else 30,
    }
    return _polish_plan_with_llm(plan, locale=locale)


# ── Disease progression analysis ────────────────────────────────────


_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


def compare_progression(*, current: dict, previous: Optional[dict]) -> dict:
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
