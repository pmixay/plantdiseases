"""
PlantVision — AI / smart-API services.

Houses the supporting endpoints layered on top of the disease classifier:

* **PlantNet identify** — ``identify_species(image_bytes)`` calls the
  PlantNet ``/v2/identify`` endpoint when a ``PLANTNET_API_KEY`` is set,
  with a server-side LRU cache keyed on a SHA-256 of the image so we
  don't burn quota re-identifying the same photo within a deploy.

* **AI Plant Doctor** — ``chat_reply()`` runs an OpenRouter chat
  completion (default model: ``openai/gpt-oss-120b``). Per-session
  conversation buffer + per-session budget cap (token + message) so a
  rogue client can't drain the API budget.

* **Care plan** — ``generate_care_plan()`` deterministic rules-based
  plan, optionally polished by the OpenRouter LLM when an API key is
  configured. Always returns a usable plan even with no key set.

* **Progression analyser** — ``compare_progression()`` pure function
  comparing the current scan to a prior scan dict. Used by
  ``/api/progression`` so the Android client can render a "getting
  worse / improving / stable" trend.

All external calls are bounded by environment-driven budgets and
fail gracefully — losing PlantNet or OpenRouter must never break the
core ``/api/analyze`` flow.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    from .config import get_float, get_int, get_str  # type: ignore[import-not-found]
except ImportError:
    from config import get_float, get_int, get_str

logger = logging.getLogger(__name__)

# ── Configuration (config.json + env fallback) ──────────────────────

PLANTNET_API_KEY = get_str("PLANTNET_API_KEY", "").strip()
PLANTNET_PROJECT = get_str("PLANTNET_PROJECT", "all")
# Free tier is 500 / day. We expose 5/min as a soft client cap on top
# of the existing per-IP rate limiter to keep one deploy from burning
# the daily budget on a single bad actor.
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

# OpenRouter chat backend (single provider so one key gates many models).
OPENROUTER_API_KEY = get_str("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = get_str("OPENROUTER_MODEL", "openai/gpt-oss-120b").strip()
OPENROUTER_BASE_URL = get_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_TOTAL_TIMEOUT = get_float("OPENROUTER_TOTAL_TIMEOUT", 20.0)
# The default model (openai/gpt-oss-120b) is a REASONING model: its hidden
# reasoning tokens are billed against max_tokens. The trace length VARIES per
# call, so with a small budget it sometimes eats the whole thing and the answer
# is truncated mid-word (finish_reason="length"). Two levers keep the answer's
# room, and we use both:
#   * a generous max_tokens (see SPECIES_DISEASES_MAX_TOKENS) — headroom; and
#   * a LOW reasoning effort so the trace stays short and predictable rather
#     than variable — this is what turns "usually fits" into "reliably fits".
# Default effort is "minimal" (confirmed to return complete content on gpt-oss).
# We do NOT disable reasoning: {"reasoning": {"enabled": false}} — and effort
# "none" — is rejected outright by some providers (Novita's gpt-oss endpoint
# 400s with "Reasoning is mandatory for this endpoint and cannot be disabled").
# Set OPENROUTER_REASONING_EFFORT to any documented level
# (max/xhigh/high/medium/low/minimal/none), or to "omit" to send no reasoning
# field at all (let the model use its own default).
OPENROUTER_REASONING_EFFORT = get_str("OPENROUTER_REASONING_EFFORT", "minimal").strip().lower()

# Hard caps so a runaway client can't blow the budget. Tune via config
# in production.
CHAT_MAX_TOKENS = get_int("CHAT_MAX_TOKENS", 512)
CHAT_SESSION_TURN_CAP = get_int("CHAT_SESSION_TURN_CAP", 20)
CHAT_SESSION_TOKEN_CAP = get_int("CHAT_SESSION_TOKEN_CAP", 8000)
CHAT_SESSION_TTL_SEC = get_int("CHAT_SESSION_TTL_SEC", 3600)
# Hard cap on retained messages per session — independent of the turn
# count cap, this protects against very long single-turn messages
# accumulating unbounded memory before the TTL GC reclaims the session.
CHAT_SESSION_MAX_MESSAGES = get_int("CHAT_SESSION_MAX_MESSAGES", 60)
# How many recent messages we forward to the LLM each turn. Keeps the
# context window predictable independent of total session history.
CHAT_HISTORY_KEEP_LAST = get_int("CHAT_HISTORY_KEEP_LAST", 12)
# Hardcap on live sessions: a client spamming unique session_ids must not grow
# the map unbounded between the per-turn TTL sweeps.
CHAT_SESSION_MAX_SESSIONS = get_int("CHAT_SESSION_MAX_SESSIONS", 10000)


def _chat_api_key_present() -> bool:
    return bool(OPENROUTER_API_KEY)


# ── Generic LRU helper ──────────────────────────────────────────────


class _LRU:
    """Tiny thread-safe LRU with an optional per-cache TTL. Stdlib only."""

    def __init__(self, maxsize: int, ttl_seconds: float | None = None):
        self.maxsize = max(1, int(maxsize))
        self.ttl = float(ttl_seconds) if ttl_seconds else None
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            expiry, value = item
            if self.ttl is not None and expiry <= time.time():
                del self._d[key]          # lazily drop the stale entry
                return None
            self._d.move_to_end(key)
            return value

    def put(self, key: str, value: Any) -> None:
        expiry = (time.time() + self.ttl) if self.ttl else 0.0
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
            self._d[key] = (expiry, value)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def __len__(self) -> int:  # for /api/metrics
        with self._lock:
            return len(self._d)


# Root for persistent on-disk caches. The in-memory _LRU above is wiped on
# every restart/deploy and is per-worker, so an expensive, stable LLM result
# (e.g. a species' common diseases — a ~10-20s OpenRouter call) was paid for
# again after each restart and on every fresh app install. _DiskCache layers a
# JSON-per-key store behind the _LRU so a successful result survives restarts
# and is shared across workers.
_AI_CACHE_DIR = Path(get_str("AI_CACHE_DIR", str(Path(__file__).resolve().parent / "ai_cache")))


class _DiskCache:
    """Persistent JSON disk cache for expensive, stable results. Best-effort:
    any I/O error degrades to a miss/no-op so it never breaks the request."""

    def __init__(self, subdir: str, ttl_seconds: float | None = None):
        self.ttl = float(ttl_seconds) if ttl_seconds else None
        self.dir = _AI_CACHE_DIR / subdir

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.dir / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        try:
            with open(self._path(key), encoding="utf-8") as fh:
                env = json.load(fh)
        except (OSError, ValueError):
            return None
        if self.ttl and time.time() - float(env.get("saved_at", 0)) > self.ttl:
            return None
        return env.get("payload")

    def put(self, key: str, payload: Any) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            # Unique temp file per writer. A shared "<key>.tmp" let two concurrent
            # writers for the SAME key (a cold-cache stampede on one species)
            # interleave their JSON into one file — the published .json is then
            # corrupt, so every future read misses and re-pays the ~10-20 s LLM
            # call until a non-concurrent write happens to fix it. mkstemp gives
            # each writer its own file; os.replace publishes atomically.
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump({"saved_at": time.time(), "payload": payload}, fh, ensure_ascii=False)
                os.replace(tmp, path)  # atomic publish so a reader never sees a half-write
            except OSError:
                try:
                    os.unlink(tmp)  # don't leak the temp file on a failed write
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.debug("disk cache write failed for %s: %s", self.dir.name, exc)


def _probe_ai_cache_writable() -> bool:
    """Surface a silently-unwritable AI_CACHE_DIR at startup.

    The disk cache is best-effort (writes swallow OSError), so when AI_CACHE_DIR
    is read-only — common in containers where only ``/tmp`` is writable — the
    persistent layer silently never works: each worker falls back to its own
    in-memory LRU, so an LLM care-plan / species-diseases result another worker
    already paid for is re-fetched (~10-20 s) by every cold worker and lost on
    restart. That is exactly the "another user loaded it but it's still slow for
    me" symptom; one clear warning beats a silent perf cliff.
    """
    try:
        _AI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        probe = _AI_CACHE_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError as exc:
        logger.warning(
            "AI_CACHE_DIR %s is not writable (%s): LLM care-plan / "
            "species-diseases results will NOT persist across restarts or be "
            "shared across workers, so every cold worker re-pays the slow "
            "OpenRouter call. Point AI_CACHE_DIR at a writable (ideally shared) "
            "volume so the species card's common problems + short care plan load "
            "fast for everyone after the first fetch.",
            _AI_CACHE_DIR, exc,
        )
        return False


# Probe once at import so a misconfigured cache dir is visible in the logs.
_AI_CACHE_WRITABLE = _probe_ai_cache_writable()


_PLANTNET_CACHE = _LRU(PLANTNET_CACHE_SIZE, PLANTNET_CACHE_TTL_SECONDS)


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
        return {"available": False, "results": [], "source": "no_api_key"}

    # Normalise so we don't cache "en" and "EN" as separate entries.
    lang = (lang or "en").strip().lower()[:5] or "en"

    cache_key = f"{_image_hash(image_bytes)}:{organ}:{lang}"
    cached = _PLANTNET_CACHE.get(cache_key)
    if cached is not None:
        # Deep-copy so the caller can mutate the returned ``results``
        # list / ``common_names`` sub-list without poisoning the LRU
        # entry for the next reader (``{**cached, ...}`` is a shallow
        # copy and the nested list-of-dicts is still aliased).
        return {**copy.deepcopy(cached), "cached": True}

    try:
        # Lazy import — only the identify path needs requests.
        import requests  # noqa: WPS433

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
        payload = None
        for attempt in range(attempts):
            try:
                r = requests.post(
                    url, params=params, files=files, data=data,
                    timeout=PLANTNET_TIMEOUT,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if attempt + 1 < attempts:
                    logger.info(
                        "PlantNet attempt %d/%d failed (%s); retrying",
                        attempt + 1, attempts, type(e).__name__,
                    )
                    continue
                raise
            if r.status_code == 401:
                logger.error("PlantNet auth failed — check PLANTNET_API_KEY")
                return {"available": False, "results": [], "source": "auth_failed"}
            if r.status_code == 429:
                logger.warning("PlantNet rate-limited; surfacing graceful degraded result")
                return {"available": False, "results": [], "source": "rate_limited"}
            r.raise_for_status()
            payload = r.json()
            break
    except Exception as e:  # noqa: BLE001 — never propagate to client
        logger.warning("PlantNet identify failed: %s", _redact_key(str(e)))
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


# ── AI Plant Doctor (OpenRouter) ────────────────────────────────────


# Minimal long-form context for the LLM. Stable prefix — the OpenRouter
# default model (openai/gpt-oss-120b) doesn't need explicit cache_control
# headers to amortise it.
_DOCTOR_SYSTEM_PROMPT = """You are PlantDiseases' friendly AI Plant Doctor, helping
people care for their plants — indoor and garden alike. Your knowledge is grounded in the
PlantVision v2.x disease taxonomy (9 trainable classes):

- gray_mold_botrytis: fuzzy grey growth, fungal (Botrytis cinerea)
- healthy: no disease detected
- leaf_scorch_underwatering: crisp brown leaf margins from
  insufficient watering or transpiration shock
- leaf_spot: umbrella class for fungal and bacterial leaf spot —
  dark/angular spots, yellow halos, concentric rings (the two are
  not reliably separable from a phone photo, so they share one class)
- not_a_plant: image does not contain a plant
- nutrient_deficiency: chlorosis / interveinal yellowing — iron,
  nitrogen, magnesium etc.
- pests: sap-sucking pests — mealybugs (white cottony tufts in leaf
  axils) and scale (waxy brown/tan bumps on stems and leaf undersides),
  merged into one class as the care advice is nearly identical
- powdery_mildew: white powdery coating, fungal
- viral_disease: umbrella class for mosaic, leaf curl and ringspot;
  incurable

Remaining deferred physiological conditions (edema, sunburn) are NOT
yet trained classes — if the user's photo shows one of those patterns
the classifier will return a low-confidence verdict or `not_a_plant`.
In that case, ask the user
about watering, light and fertiliser routine and give physiological
advice from first principles, but be explicit that the model has not
diagnosed it.

Style guide:
- Keep replies SHORT (3-6 sentences). Specific, actionable, friendly.
- Match the user's language (English or Russian).
- If the user describes symptoms outside the taxonomy, say so and
  suggest taking a photo or consulting a local nursery.
- Never invent fungicide trade names. Recommend generic active
  ingredients only (e.g. "copper-based fungicide", "neem oil").
- Never claim the AI can diagnose with certainty — defer to the
  classifier output the user already has.
- When you mention a condition the app has a care guide for, cite it
  once with a token like [[doc:leaf_spot]] placed right after the name —
  the app turns it into a tappable link to the offline guide. Valid keys:
  gray_mold_botrytis, healthy, leaf_scorch_underwatering, leaf_spot,
  nutrient_deficiency, pests, powdery_mildew, viral_disease, edema,
  sunburn_or_scorch. Use a key at most once per reply, only when relevant,
  and never invent keys outside this list."""


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

    def append(self, role: str, content: str) -> None:
        """Append a message and enforce CHAT_SESSION_MAX_MESSAGES."""
        self.messages.append({"role": role, "content": content})
        excess = len(self.messages) - CHAT_SESSION_MAX_MESSAGES
        if excess > 0:
            del self.messages[:excess]


_chat_sessions: dict[str, _ChatSession] = {}
_chat_lock = threading.Lock()
# Server-wide totals for the abuse log / Prometheus.
_chat_totals = {"requests": 0, "rejected": 0, "tokens_in": 0, "tokens_out": 0}


_OPENROUTER_TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_OPENROUTER_MAX_RETRIES = 3
_OPENROUTER_BASE_DELAY = 1.0  # seconds
# OpenRouter's documented reasoning.effort levels (passed through verbatim).
_OPENROUTER_REASONING_EFFORTS = frozenset(
    {"max", "xhigh", "high", "medium", "low", "minimal", "none"}
)


def _call_openrouter(
    system_text: str, history: list[dict],
    *, max_tokens: int | None = None, require_complete: bool = False,
    reasoning_effort: str | None = None,
) -> tuple[str, int, int]:
    """Run an OpenRouter chat completion with bounded retry.

    Retries up to ``_OPENROUTER_MAX_RETRIES`` times with exponential
    backoff (1 s / 2 s / 4 s) on:
      * HTTP 408 / 425 / 429 / 5xx — transient upstream failures.
      * ``requests.ConnectionError`` / ``Timeout`` — network blips.
    Honours the ``Retry-After`` header on 429 if it parses as seconds.
    4xx other than 408/425/429 (auth, bad request) fail immediately —
    retrying those just wastes the user's turn budget.

    ``require_complete`` raises when the model stops on ``finish_reason=
    "length"`` (the answer was cut off mid-token). Callers that cache or parse
    structured output pass this so a truncated reply is rejected, not cached or
    parsed-as-empty.

    ``reasoning_effort`` overrides the global ``OPENROUTER_REASONING_EFFORT``
    for this call only. Structured-output callers (species diseases, care-plan
    polish) pin "minimal" so an operator raising the global effort for the
    conversational doctor can't reinflate their reasoning traces back into
    ``finish_reason="length"`` truncation.
    """
    import requests  # noqa: WPS433

    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.extend(history)

    payload = {
        "model": OPENROUTER_MODEL,
        "max_tokens": max_tokens or CHAT_MAX_TOKENS,
        "messages": messages,
    }
    # Bound the reasoning trace so it can't starve the answer: by default send a
    # low effort ("minimal") so the trace stays short, alongside the generous
    # max_tokens headroom. A recognised effort level is forwarded; any other
    # value (e.g. "omit") sends no reasoning field. We don't default to disabling
    # reasoning — {"enabled": false} / effort "none" is rejected by some
    # providers (see OPENROUTER_REASONING_EFFORT). reasoning.exclude is unused:
    # it hides the trace but the model still reasons + bills, so it can't help.
    effort = (reasoning_effort or OPENROUTER_REASONING_EFFORT).strip().lower()
    if effort in _OPENROUTER_REASONING_EFFORTS:
        payload["reasoning"] = {"effort": effort}
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # OpenRouter recommends these for attribution but they are optional.
        "HTTP-Referer": "https://github.com/pmixay/plantdiseases",
        "X-Title": "PlantDiseases",
    }

    last_exc: Exception | None = None
    start_time = time.time()
    for attempt in range(_OPENROUTER_MAX_RETRIES):
        elapsed = time.time() - start_time
        remaining = OPENROUTER_TOTAL_TIMEOUT - elapsed
        if remaining <= 0.5:
            if last_exc:
                raise last_exc
            raise requests.Timeout("OpenRouter request timed out (total budget exceeded)")
        
        attempt_timeout = max(1.0, remaining)
        try:
            r = requests.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers, json=payload, timeout=attempt_timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt + 1 == _OPENROUTER_MAX_RETRIES:
                raise
            _sleep_for_retry(attempt, retry_after=None)
            continue

        if r.status_code in _OPENROUTER_TRANSIENT_STATUSES:
            if attempt + 1 == _OPENROUTER_MAX_RETRIES:
                r.raise_for_status()  # last-ditch — caller handles
            _sleep_for_retry(attempt, retry_after=r.headers.get("Retry-After"))
            continue
        if r.status_code >= 400:
            r.raise_for_status()  # 4xx (not in transient set) — fail fast

        data = r.json()
        choices = data.get("choices") or []
        text = ""
        finish_reason = ""
        if choices:
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            finish_reason = choices[0].get("finish_reason") or ""
        usage = data.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        if require_complete and finish_reason == "length":
            # Hit max_tokens mid-answer. Returning the partial would cache/serve a
            # reply cut off mid-word (truncated JSON parses as empty; a truncated
            # care summary slips past the dosage guard), so treat it as a failure
            # and let the caller fall back to its deterministic content.
            raise RuntimeError(
                f"OpenRouter response truncated (finish_reason=length, "
                f"completion_tokens={tout}); raise max_tokens or lower reasoning"
            )
        return text, tin, tout

    # Loop only exits on transient failure with retries exhausted.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("OpenRouter retries exhausted without a successful response")


def _sleep_for_retry(attempt: int, retry_after: str | None) -> None:
    """Sleep before the next OpenRouter retry. Honours a numeric
    ``Retry-After`` header when present, otherwise falls back to
    exponential backoff capped at 8 s."""
    delay = _OPENROUTER_BASE_DELAY * (2 ** attempt)
    if retry_after:
        try:
            server_wait = float(retry_after)
            if server_wait > 0:
                delay = max(delay, min(server_wait, 8.0))
        except ValueError:
            pass
    time.sleep(min(delay, 8.0))


def _gc_chat_sessions(now: float) -> None:
    stale = [
        sid for sid, sess in _chat_sessions.items()
        if now - sess.last_active > CHAT_SESSION_TTL_SEC
    ]
    for sid in stale:
        del _chat_sessions[sid]
    # Hardcap even within TTL: evict the least-recently-active beyond the cap
    # so a flood of unique session_ids can't OOM the process before the sweep.
    over = len(_chat_sessions) - CHAT_SESSION_MAX_SESSIONS
    if over > 0:
        for sid in sorted(
            _chat_sessions, key=lambda s: _chat_sessions[s].last_active
        )[:over]:
            del _chat_sessions[sid]


_DIAG_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}(:[a-z0-9_]{1,40})?$")


def _sanitize_diagnosis_context(ctx: dict) -> dict:
    """Coerce the (client-supplied) diagnosis context into a strict
    shape before it is embedded in the system prompt.

    ``/api/chat`` callers control ``diagnosis_context``, so the raw
    ``class`` / ``warnings`` strings are an untrusted prompt-injection
    surface (free text in a *system* message carries the most weight).
    We keep only values that match a tight token shape — lowercase
    identifiers, no whitespace, no newlines — so no natural-language
    instruction can ride along, and clamp confidence to [0, 1].
    """
    raw_class = ctx.get("class_name") or ctx.get("disease_name") or ""
    cls = raw_class if _DIAG_TOKEN_RE.match(str(raw_class)) else None

    severity = ctx.get("severity")
    if severity not in ("low", "medium", "high"):
        severity = None

    try:
        conf = float(ctx.get("confidence"))
        conf = round(max(0.0, min(1.0, conf)), 4)
    except (TypeError, ValueError):
        conf = None

    warnings: list[str] = []
    for w in (ctx.get("warnings") or [])[:8]:
        if isinstance(w, str) and _DIAG_TOKEN_RE.match(w):
            warnings.append(w)

    # Species is a plant NAME (spaces, capitals, Cyrillic) so the strict
    # lowercase-token filter can't apply — but it's still client-supplied, so
    # drop newlines/control chars (an injected value can't break out of its
    # JSON line into a fake system instruction), collapse whitespace, and cap
    # length. It stays a labeled data value the model reads as context.
    species = None
    raw_species = ctx.get("species")
    if isinstance(raw_species, str):
        cleaned = " ".join(raw_species.split())
        cleaned = "".join(ch for ch in cleaned if ch.isprintable())[:80].strip()
        species = cleaned or None

    return {
        "class": cls, "species": species, "confidence": conf,
        "severity": severity, "warnings": warnings,
    }


def _build_system_text(locale: str, diagnosis_context: dict | None) -> str:
    """Compose the system prompt: doctor instructions + locale hint +
    optional structured diagnosis context. Plain string so OpenRouter
    receives one clean system message."""
    locale_hint = (
        "Reply in Russian." if locale.lower().startswith("ru")
        else "Reply in English."
    )
    parts = [_DOCTOR_SYSTEM_PROMPT, locale_hint]
    if diagnosis_context:
        compact = _sanitize_diagnosis_context(diagnosis_context)
        parts.append("Latest classifier output:\n" + json.dumps(compact, ensure_ascii=False))
    return "\n\n".join(parts)


def chat_reply(
    *,
    session_id: str,
    user_message: str,
    locale: str = "en",
    diagnosis_context: dict | None = None,
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
        history = sess.trim_for_context(keep_last=CHAT_HISTORY_KEEP_LAST)

    system_text = _build_system_text(locale, diagnosis_context)
    user_blocks = list(history) + [{"role": "user", "content": user_message}]

    try:
        reply_text, tin, tout = _call_openrouter(system_text, user_blocks)
    except Exception as e:  # noqa: BLE001
        logger.warning("OpenRouter chat failed: %s", e)
        return {"available": False, "reply": "", "reason": "api_error"}

    if not reply_text:
        # Empty completion (provider stutter, content filter, etc.).
        # Don't burn the user's turn or token budget on it — the client
        # will resurface the prompt and retry.
        logger.warning("OpenRouter returned empty reply")
        return {"available": False, "reply": "", "reason": "empty_reply"}

    with _chat_lock:
        sess.append("user", user_message)
        sess.append("assistant", reply_text)
        sess.tokens_in += tin
        sess.tokens_out += tout
        sess.turn_count += 1
        sess.last_active = time.time()
        _chat_totals["requests"] += 1
        _chat_totals["tokens_in"] += tin
        _chat_totals["tokens_out"] += tout
        # Snapshot session counters inside the lock — another concurrent
        # turn for the same session_id would otherwise mutate them
        # between the increment and the response build.
        turns_used = sess.turn_count
        tokens_used = sess.tokens_in + sess.tokens_out
        budget_left_tokens = max(0, CHAT_SESSION_TOKEN_CAP - tokens_used)
        budget_left_turns = max(0, CHAT_SESSION_TURN_CAP - turns_used)

    return {
        "available": True,
        "reply": reply_text,
        "model": OPENROUTER_MODEL,
        "provider": "openrouter",
        "usage": {"input_tokens": tin, "output_tokens": tout},
        "session": {
            "session_id": session_id,
            "turns_used": turns_used,
            "turns_remaining": budget_left_turns,
            "tokens_used": tokens_used,
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


# ── Species common-diseases (OpenRouter, structured) ────────────────
#
# Powers the species-detail screen's "common problems" section. There is
# no per-species disease dataset in the project, so we ask the chat model
# for the conditions a species is most commonly affected by indoors and
# return a small STRUCTURED list. Each item may carry a ``doc_key`` from
# the app's guide taxonomy so the client renders a tappable link into its
# offline disease guide; unknown keys are dropped to null (plain text).
#
# Best-effort and additive: no API key (or any failure) returns
# ``{"available": False}`` and the client falls back to its offline guide
# list, so the section is never empty.

SPECIES_DISEASES_CACHE_SIZE = get_int("SPECIES_DISEASES_CACHE_SIZE", 256)
SPECIES_DISEASES_CACHE_TTL_SECONDS = get_int("SPECIES_DISEASES_CACHE_TTL_SECONDS", 7 * 24 * 3600)
# Headroom for the answer even if a reasoning model still spends some budget
# reasoning (max_tokens is a cap, not a target — short answers stop early and
# only bill what they generate, so a higher cap costs nothing but avoids the
# truncation that returned an empty list and re-paid the call on every open).
# 1024 was still hit in production when the model's hidden trace ran long
# (the trace bills against max_tokens), so the default carries 2× headroom.
SPECIES_DISEASES_MAX_TOKENS = get_int("SPECIES_DISEASES_MAX_TOKENS", 2048)
_SPECIES_DISEASES_CACHE = _LRU(SPECIES_DISEASES_CACHE_SIZE, SPECIES_DISEASES_CACHE_TTL_SECONDS)
# Persistent layer behind the LRU: the species→diseases LLM answer is stable,
# so once paid for it's reused across restarts and fresh installs instead of
# re-running the ~10-20s OpenRouter call every time.
_SPECIES_DISEASES_DISK = _DiskCache("species_diseases", SPECIES_DISEASES_CACHE_TTL_SECONDS)

# Disease-guide keys the Android client can deep-link (mirrors the app's
# DiseaseCatalog). The model may tag an item with one of these; anything
# else becomes null so the client shows the item as plain text.
_GUIDE_DOC_KEYS = frozenset({
    "gray_mold_botrytis", "leaf_scorch_underwatering", "leaf_spot",
    "nutrient_deficiency", "pests", "powdery_mildew", "viral_disease",
    "edema", "sunburn_or_scorch",
})

_SPECIES_DISEASES_SYSTEM = """You are a plant pathology assistant. Given a plant \
species, list the problems (diseases, sap-sucking pests, physiological disorders) that \
species is MOST commonly affected by when grown indoors, most common first.

Return ONLY a JSON object, no prose, of exactly this shape:
{"diseases": [{"name": "<short condition name>", "doc_key": "<an allowed key or null>", \
"note": "<one short sentence: how it shows on this plant + the single most useful first action>"}]}

Allowed doc_key values (use the closest match, otherwise null):
gray_mold_botrytis, leaf_scorch_underwatering, leaf_spot, nutrient_deficiency, pests, \
powdery_mildew, viral_disease, edema, sunburn_or_scorch

Rules:
- 3 to 5 items, most common first.
- Write "name" and "note" in the requested language.
- Keep each "note" under 20 words. Never name fungicide trade names — generic actives only.
- Only conditions plausible for that species indoors; do not invent exotic pathogens.
- Output the JSON object only, nothing before or after it."""


def _parse_species_diseases(text: str) -> list[dict]:
    """Extract + sanitise the diseases list from the model's JSON reply.

    Tolerant of code fences / leading prose (grabs the first ``{...}``
    block). Drops malformed items and caps lengths so a misbehaving model
    can't push junk to the client. Returns ``[]`` on any problem.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    items = data.get("diseases") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name or len(name) > 80:
            continue
        note = str(it.get("note") or "").strip()
        if len(note) > 240:
            note = note[:240].rstrip()
        doc_key = it.get("doc_key")
        doc_key = doc_key if doc_key in _GUIDE_DOC_KEYS else None
        out.append({"name": name, "doc_key": doc_key, "note": note})
    return out


def species_common_diseases(
    scientific_name: str | None,
    *,
    common_name: str | None = None,
    lang: str = "en",
) -> dict:
    """Conditions a species is commonly affected by indoors (best-effort, AI).

    Returns ``{"available": True, "species", "diseases": [...]}`` where each
    disease is ``{"name", "doc_key"|None, "note"}``. Returns
    ``{"available": False, "diseases": []}`` when no OpenRouter key is set or
    on any failure — the client then renders its offline disease guide, so the
    species screen's "common problems" section is never empty. Never raises.
    """
    name = (scientific_name or "").strip()
    if not name:
        return {"available": False, "diseases": [], "source": "no_species"}
    lang = "ru" if _is_russian(lang) else "en"
    if not _chat_api_key_present():
        return {"available": False, "diseases": [], "source": "no_api_key"}

    cache_key = f"{name.lower()}:{lang}"
    cached = _SPECIES_DISEASES_CACHE.get(cache_key)
    if cached is not None:
        return {**copy.deepcopy(cached), "cached": True}
    # Fall through to the persistent layer before paying for the LLM again —
    # this is what survives a server restart / a fresh app install.
    disk_hit = _SPECIES_DISEASES_DISK.get(cache_key)
    if disk_hit is not None:
        _SPECIES_DISEASES_CACHE.put(cache_key, disk_hit)
        return {**copy.deepcopy(disk_hit), "cached": True}

    lang_word = "Russian" if lang == "ru" else "English"
    common = (common_name or "").strip()
    subject = name + (f" (commonly known as {common})" if common else "")
    user = (
        f"Species: {subject}\n"
        f"Write the names and notes in {lang_word}.\n"
        "Return the JSON object now."
    )
    try:
        text, _tin, _tout = _call_openrouter(
            _SPECIES_DISEASES_SYSTEM,
            [{"role": "user", "content": user}],
            max_tokens=SPECIES_DISEASES_MAX_TOKENS,
            require_complete=True,
            # Pin the shortest trace: this call needs structured recall, not
            # reasoning, and a long trace is what pushes it into truncation.
            reasoning_effort="minimal",
        )
    except Exception as e:  # noqa: BLE001 — never propagate to the client
        logger.warning("species_common_diseases failed: %s", e)
        return {"available": False, "diseases": [], "source": "api_error"}

    diseases = _parse_species_diseases(text)
    if not diseases:
        return {"available": False, "diseases": [], "source": "empty"}
    response = {
        "available": True,
        "species": name,
        "diseases": diseases,
        "source": "openrouter",
    }
    _SPECIES_DISEASES_CACHE.put(cache_key, response)
    _SPECIES_DISEASES_DISK.put(cache_key, response)
    return response


# ── Care plan ───────────────────────────────────────────────────────
#
# The care-plan generator is the only deterministic surface in this
# module — it must produce a usable plan even when the OpenRouter key
# is missing. The rule engine below is built around four primitives:
#
#   * **Light tier** — translates the user-supplied light bucket (low /
#     medium / bright_indirect / direct_sun) into concrete lux targets,
#     window placement guidance, and acclimation notes.
#   * **Base care** — watering interval, watering method, soil mix,
#     fertiliser cadence + NPK, humidity band, day/night temperature
#     window. These are the "if the plant were healthy" defaults.
#   * **Season modifier** — growing season (Mar–Oct N-hemi default)
#     vs dormant. Reduces watering / feeding intensity in dormant
#     months to prevent rot.
#   * **Disease protocol** — per-class overrides + an explicit
#     treatment checklist, sanitation steps, isolation flag and red
#     flags (escalate-to-doctor symptoms).
#
# Backwards-compat: the v1 caller (Android v1.x) reads
# ``watering_days``, ``fertilise_weeks``, ``humidity_target``,
# ``key_action`` and ``follow_up_days``. Those five keys keep their
# v1 meaning. The richer fields are additive — clients that ignore
# unknown keys still render the v1 card unchanged.

_VALID_LIGHT_TIERS: tuple[str, ...] = (
    "low_light",
    "medium_light",
    "bright_indirect",
    "direct_sun",
)

# Light tier → (target lux band, window placement hint, acclimation
# hint). Lux is the canonical unit so the LightMeterActivity readout
# can be compared directly.
_LIGHT_TIER_PROFILE: dict[str, dict] = {
    "low_light": {
        "lux_target": {"min": 50, "max": 250},
        "placement": "north-facing window or 2-3 m back from a bright window",
        "acclimation": "Tolerates shade indefinitely; never move suddenly into direct sun.",
    },
    "medium_light": {
        "lux_target": {"min": 250, "max": 1_000},
        "placement": "east-facing window or 1-2 m back from a south/west window",
        "acclimation": "Rotate the pot a quarter-turn weekly for even growth.",
    },
    "bright_indirect": {
        "lux_target": {"min": 1_000, "max": 5_000},
        "placement": "1 m from a south/west window with a sheer curtain",
        "acclimation": "Acclimate over 7-10 days when moving between light tiers.",
    },
    "direct_sun": {
        "lux_target": {"min": 5_000, "max": 25_000},
        "placement": "south or west window with no curtain, or outdoors after acclimation",
        "acclimation": "Acclimate slowly (1 h/day extra) to avoid sunburn.",
    },
}

# Base care defaults per light tier (assumed healthy plant). All
# numeric ranges are inclusive.
_BASE_CARE: dict[str, dict] = {
    "low_light": {
        "watering_days": 10,
        "watering_method": "soak_and_drain",
        "soil_mix": "1 part peat-based mix + 1 part perlite for drainage",
        "fertilise_weeks": 6,
        "fertiliser_npk": "balanced 3-1-2 at half strength",
        "humidity_band": {"min": 40, "max": 60},
        "temperature_c": {"day": [18, 24], "night": [15, 20]},
    },
    "medium_light": {
        "watering_days": 7,
        "watering_method": "soak_and_drain",
        "soil_mix": "2 parts peat or coco + 1 part perlite + 1 part bark",
        "fertilise_weeks": 4,
        "fertiliser_npk": "balanced 3-1-2 at half strength",
        "humidity_band": {"min": 40, "max": 60},
        "temperature_c": {"day": [18, 26], "night": [15, 21]},
    },
    "bright_indirect": {
        "watering_days": 5,
        "watering_method": "soak_and_drain",
        "soil_mix": "2 parts peat or coco + 1 part perlite + 1 part bark",
        "fertilise_weeks": 3,
        "fertiliser_npk": "balanced 3-1-2 at full strength",
        "humidity_band": {"min": 50, "max": 70},
        "temperature_c": {"day": [20, 27], "night": [16, 22]},
    },
    "direct_sun": {
        "watering_days": 4,
        "watering_method": "soak_and_drain",
        "soil_mix": "fast-draining cactus/succulent mix or 1:1 with grit",
        "fertilise_weeks": 3,
        "fertiliser_npk": "low-N 2-3-3 to support hardening off",
        "humidity_band": {"min": 30, "max": 50},
        "temperature_c": {"day": [22, 30], "night": [16, 22]},
    },
}

# Disease-specific overrides. Anything missing falls back to the base
# light-tier plan. ``treatment`` is the per-disease checklist that
# replaces the v1 single-line ``note``; ``sanitation`` and ``red_flags``
# are surfaced as their own client sections.
_DISEASE_PROTOCOL: dict[str, dict] = {
    "powdery_mildew": {
        "key_action": "Lower humidity, improve airflow, and dust spores with a baking-soda spray.",
        "humidity_band": {"min": 30, "max": 45},
        "watering_method": "bottom_water",
        "treatment": [
            "Move the plant to a brighter, drier spot with airflow.",
            "Wipe visibly infected leaves with a 1:9 milk-water solution once a week.",
            "If it spreads, apply potassium-bicarbonate or sulfur fungicide per label.",
            "Discard heavily infected leaves into a sealed bag — do not compost.",
        ],
        "sanitation": ["Sanitise pruners with 70% isopropyl between cuts."],
        "isolation": True,
        "red_flags": [
            "White fuzz reappears within 7 days of treatment.",
            "Spreads to a second plant despite isolation.",
        ],
        "follow_up_days": 7,
        "fertilise_weeks": 6,
    },
    "gray_mold_botrytis": {
        "key_action": "Lower humidity, clear debris weekly, and apply copper or biofungicide.",
        "humidity_band": {"min": 30, "max": 50},
        "watering_method": "bottom_water",
        "treatment": [
            "Remove all fallen / mushy leaves and discard in a sealed bag.",
            "Cut visibly infected tissue back to clean green, sterilising the blade between cuts.",
            "Apply a copper-octanoate or Bacillus-subtilis spray every 7 days for 3 weeks.",
            "Run a fan nearby for 4–6 hours/day to keep leaves dry.",
        ],
        "sanitation": [
            "Wash the saucer and outer pot with 1:9 bleach-water.",
            "Top-dress the soil with fresh, dry medium to bury spores.",
        ],
        "isolation": True,
        "red_flags": [
            "New gray fuzz appears on stems or growth points.",
            "Leaves continue to collapse despite drier conditions.",
        ],
        "follow_up_days": 7,
        "fertilise_weeks": 6,
    },
    "leaf_spot": {
        "key_action": "Remove infected leaves, stop overhead watering, and apply a copper-based spray weekly.",
        "watering_method": "bottom_water",
        "treatment": [
            "Strip all leaves with >25% spot coverage; discard sealed.",
            "Stop all overhead watering and misting — keep the foliage dry.",
            "Spray copper-hydroxide (covers fungal and bacterial) or chlorothalonil weekly for 3–4 weeks per label dose.",
            "Improve airflow with a fan during the wettest part of the day.",
            "Isolate the plant until new growth comes in clean.",
        ],
        "sanitation": [
            "Sterilise pruners with 70% isopropyl between cuts; do not reuse infected soil.",
            "Wash hands between handling this plant and your other plants.",
        ],
        "isolation": True,
        "red_flags": [
            "Spots expand or new spots appear after 14 days.",
            "Lesions ooze or smell bad, or the stem darkens at the soil line — bacterial soft rot.",
        ],
        "follow_up_days": 7,
    },
    "viral_disease": {
        "key_action": "Isolate the plant. Sanitise tools. There is no chemical cure.",
        "treatment": [
            "Quarantine the plant at least 2 m from other plants.",
            "Sterilise pruners with 70% isopropyl before touching any other plant.",
            "Watch for symptoms on neighbours for 30 days; cull if symptoms appear.",
            "Discard the infected plant if symptoms spread to >50% of foliage.",
        ],
        "sanitation": [
            "Wash hands with soap after handling the infected plant.",
            "Do not propagate cuttings or share the soil.",
        ],
        "isolation": True,
        "red_flags": [
            "Stunted new growth or mosaic patterns on emerging leaves.",
            "Symptoms appearing on a neighbour within 30 days.",
        ],
        "follow_up_days": 14,
        "fertilise_weeks": 0,
    },
    "edema": {
        "key_action": "Reduce watering frequency, raise light and airflow.",
        "watering_days": "as_needed",
        "watering_method": "let_top_inch_dry",
        "treatment": [
            "Skip the next watering and wait for the top 3 cm of soil to dry fully.",
            "Move to brighter light to raise transpiration.",
            "Improve airflow with a fan or open window.",
            "Avoid fertilising until new growth comes in clean.",
        ],
        "isolation": False,
        "red_flags": [
            "Blisters keep forming on new growth.",
            "Yellowing or stem softening appears.",
        ],
        "follow_up_days": 14,
        "fertilise_weeks": 0,
    },
    "sunburn_or_scorch": {
        "key_action": "Move out of direct midday sun. Bright indirect light only.",
        "treatment": [
            "Move the plant 1–2 m away from any direct south/west sun.",
            "Trim leaves with >50% burn back to the petiole.",
            "Resume a light fertiliser schedule once new growth appears.",
        ],
        "isolation": False,
        "red_flags": [
            "Burn keeps spreading even in shade — check for a root-rot underlay.",
        ],
        "follow_up_days": 14,
    },
    "leaf_scorch_underwatering": {
        "key_action": "Soak-water; flush salts; raise humidity.",
        "watering_days": "as_needed",
        "watering_method": "bottom_water_soak",
        "treatment": [
            "Bottom-soak the rootball in tepid water for 20 minutes.",
            "Flush the topsoil with 2× the pot volume of water to wash out salts.",
            "Move to a slightly shadier spot for 7 days while the plant recovers.",
            "Raise local humidity (pebble tray or humidifier) to 50–60%.",
        ],
        "isolation": False,
        "red_flags": [
            "Crispy edges keep appearing on new leaves.",
            "Soil pulls away from the pot wall — root system has collapsed.",
        ],
        "follow_up_days": 7,
    },
    "nutrient_deficiency": {
        "key_action": "Feed half-strength balanced NPK; chelated Fe + Mg for interveinal yellowing.",
        "fertilise_weeks": 2,
        "fertiliser_npk": "balanced 3-1-2 at half strength + chelated iron if interveinal",
        "treatment": [
            "Apply a balanced 3-1-2 NPK at half strength with the next watering.",
            "If interveinal yellowing (yellow leaf, green veins): add chelated iron + Epsom salt at label dose.",
            "If older leaves yellow first: nitrogen deficit — switch to a higher-N feed for 2 cycles.",
            "Reassess in 14 days; new leaves should emerge fully green.",
        ],
        "isolation": False,
        "red_flags": [
            "Yellowing spreads despite feeding — check for root rot or pH drift.",
        ],
        "follow_up_days": 14,
    },
    "healthy": {
        "key_action": "Keep your current routine; monitor weekly.",
        "treatment": [
            "Continue the current watering and feeding cadence.",
            "Wipe leaves with a damp cloth monthly to clear dust.",
            "Rotate the pot a quarter-turn each week for even growth.",
        ],
        "isolation": False,
        "red_flags": [
            "New leaves emerge smaller or paler than usual.",
            "Any spots, stickiness, webbing or new leaf-edge crisping.",
        ],
        "follow_up_days": 30,
    },
    "not_a_plant": {
        "key_action": "Try a clearer photo of a single leaf.",
        "treatment": [
            "Move closer so a single leaf fills most of the frame.",
            "Wait for autofocus to lock and re-shoot in good, even light.",
        ],
        "isolation": False,
        "follow_up_days": 30,
    },
}


# Russian counterparts for every translatable string in
# ``_DISEASE_PROTOCOL`` / ``_LIGHT_TIER_PROFILE`` / ``_BASE_CARE``.
# Only the strings change — numeric ranges, isolation flags and
# follow-up cadences are reused as-is from the canonical English
# tables above. Pulled into a separate dict so the English copy
# stays the single source of truth for the rule logic.

_DISEASE_PROTOCOL_RU: dict[str, dict] = {
    "powdery_mildew": {
        "key_action": "Снизьте влажность, улучшите проветривание и протрите листья содовым раствором.",
        "treatment": [
            "Перенесите растение в более светлое и сухое место с движением воздуха.",
            "Раз в неделю протирайте поражённые листья раствором молока и воды 1:9.",
            "Если распространяется — применяйте калия бикарбонат или серу по инструкции.",
            "Сильно поражённые листья выбрасывайте в пакете — не в компост.",
        ],
        "sanitation": ["Дезинфицируйте секатор изопропиловым 70%-м спиртом между срезами."],
        "red_flags": [
            "Белый налёт возвращается в течение 7 дней после обработки.",
            "Болезнь перешла на соседнее растение, несмотря на изоляцию.",
        ],
    },
    "gray_mold_botrytis": {
        "key_action": "Снизьте влажность, убирайте опавшие листья и применяйте препараты меди или биофунгицид.",
        "treatment": [
            "Удалите все опавшие и подгнившие листья и выбросьте в пакете.",
            "Срежьте видимо поражённые ткани до здоровой зелёной, стерилизуя лезвие между срезами.",
            "Каждые 7 дней в течение 3 недель опрыскивайте меди октаноатом или Bacillus subtilis.",
            "Включайте вентилятор рядом 4–6 часов в день, чтобы листья оставались сухими.",
        ],
        "sanitation": [
            "Промойте поддон и горшок раствором отбеливателя 1:9.",
            "Присыпьте субстрат слоем свежего сухого грунта, чтобы заглубить споры.",
        ],
        "red_flags": [
            "Новый серый налёт появляется на стеблях или точках роста.",
            "Листья продолжают опадать даже при более сухих условиях.",
        ],
    },
    "leaf_spot": {
        "key_action": "Удалите поражённые листья, прекратите полив сверху и применяйте препарат меди раз в неделю.",
        "treatment": [
            "Срежьте все листья с пятнистостью более 25% и выбросьте в пакете.",
            "Прекратите полив сверху и опрыскивание — держите листву сухой.",
            "Раз в неделю в течение 3–4 недель применяйте гидроксид меди (подходит и против грибка, и против бактерий) или хлороталонил по инструкции.",
            "Включайте вентилятор в самое влажное время дня.",
            "Изолируйте растение до появления чистого нового прироста.",
        ],
        "sanitation": [
            "Стерилизуйте секатор 70%-м изопропиловым спиртом между срезами; не используйте заражённый грунт повторно.",
            "Мойте руки между этим растением и другими.",
        ],
        "red_flags": [
            "Через 14 дней пятна разрастаются или появляются новые.",
            "Очаги мокнут или плохо пахнут, либо стебель темнеет у почвы — бактериальная мягкая гниль.",
        ],
    },
    "viral_disease": {
        "key_action": "Изолируйте растение. Стерилизуйте инструменты. Химического лечения нет.",
        "treatment": [
            "Перенесите растение в карантин не ближе 2 м от других.",
            "Перед работой с другими растениями стерилизуйте секатор 70%-м изопропиловым спиртом.",
            "30 дней наблюдайте за соседями; при появлении симптомов — утилизируйте.",
            "Если симптомы охватили более 50% листвы — утилизируйте растение целиком.",
        ],
        "sanitation": [
            "Мойте руки с мылом после контакта с заражённым растением.",
            "Не используйте черенки и не передавайте грунт.",
        ],
        "red_flags": [
            "Угнетённый рост или мозаичные узоры на новых листьях.",
            "Симптомы появились у соседнего растения в течение 30 дней.",
        ],
    },
    "edema": {
        "key_action": "Сократите частоту полива, добавьте света и движения воздуха.",
        "treatment": [
            "Пропустите следующий полив и дайте верхним 3 см почвы полностью просохнуть.",
            "Перенесите в более светлое место для усиления транспирации.",
            "Усильте движение воздуха вентилятором или открытым окном.",
            "Не подкармливайте до появления чистого нового прироста.",
        ],
        "red_flags": [
            "Пузыри продолжают появляться на новом приросте.",
            "Появилось пожелтение или размягчение стебля.",
        ],
    },
    "sunburn_or_scorch": {
        "key_action": "Уберите с прямого полуденного солнца. Только яркий рассеянный свет.",
        "treatment": [
            "Перенесите растение на 1–2 м от прямого южного/западного солнца.",
            "Обрежьте листья с ожогом более 50% до черешка.",
            "Возобновите лёгкую подкормку, когда появится новый прирост.",
        ],
        "red_flags": [
            "Ожог продолжает распространяться даже в тени — проверьте корни на гниль.",
        ],
    },
    "leaf_scorch_underwatering": {
        "key_action": "Глубокий полив снизу; промывка от солей; повышение влажности.",
        "treatment": [
            "На 20 минут поставьте горшок в тазик с тёплой водой для замачивания корневого кома.",
            "Промойте верхний слой почвы количеством воды равным двойному объёму горшка, чтобы вымыть соли.",
            "На 7 дней перенесите в чуть более тенистое место для восстановления.",
            "Повысьте локальную влажность поддоном с галькой или увлажнителем до 50–60%.",
        ],
        "red_flags": [
            "Сухие края продолжают появляться на новых листьях.",
            "Почва отстаёт от стенок горшка — корневая система пострадала.",
        ],
    },
    "nutrient_deficiency": {
        "key_action": "Подкормка половинной дозой балансированного NPK; хелаты Fe + Mg при межжилковом хлорозе.",
        "fertiliser_npk": "сбалансированный 3-1-2 в половинной дозе + хелат железа при межжилковом хлорозе",
        "treatment": [
            "При следующем поливе внесите сбалансированный NPK 3-1-2 в половинной дозе.",
            "При межжилковом хлорозе (жёлтый лист, зелёные жилки): добавьте хелат железа + соль Эпсома по инструкции.",
            "Если первыми желтеют старые листья — дефицит азота; на 2 цикла перейдите на азотную подкормку.",
            "Через 14 дней повторите оценку; новые листья должны выходить ровно зелёными.",
        ],
        "red_flags": [
            "Пожелтение продолжается даже при подкормке — проверьте корни и pH.",
        ],
    },
    "healthy": {
        "key_action": "Поддерживайте текущий режим, наблюдайте раз в неделю.",
        "treatment": [
            "Сохраняйте текущий режим полива и подкормки.",
            "Раз в месяц протирайте листья влажной тряпкой от пыли.",
            "Раз в неделю поворачивайте горшок на четверть для равномерного роста.",
        ],
        "red_flags": [
            "Новые листья выходят мельче или бледнее обычного.",
            "Любые пятна, липкость, паутинка или сухие края.",
        ],
    },
    "not_a_plant": {
        "key_action": "Попробуйте сделать более чёткое фото одного листа.",
        "treatment": [
            "Подойдите ближе, чтобы один лист занимал большую часть кадра.",
            "Дождитесь автофокуса и снимите при ровном освещении.",
        ],
    },
}

# Russian copy for the light-tier text fields. Lux bands, humidity
# bands and temperature ranges keep the English table's numbers.
_LIGHT_TIER_PROFILE_RU: dict[str, dict] = {
    "low_light": {
        "placement": "северное окно или 2–3 м от яркого окна",
        "acclimation": "Переносит тень неограниченно долго; не выносите резко на прямое солнце.",
    },
    "medium_light": {
        "placement": "восточное окно или 1–2 м от южного/западного окна",
        "acclimation": "Раз в неделю поворачивайте горшок на четверть для равномерного роста.",
    },
    "bright_indirect": {
        "placement": "1 м от южного/западного окна с лёгкой занавеской",
        "acclimation": "Привыкание 7–10 дней при переходе между уровнями освещения.",
    },
    "direct_sun": {
        "placement": "южное или западное окно без занавески или на улице после акклиматизации",
        "acclimation": "Приучайте к солнцу медленно (+1 ч в день), чтобы избежать ожогов.",
    },
}

# Russian copy for the small handful of base-care strings that live
# in plain English in ``_BASE_CARE``.
_BASE_CARE_TEXT_RU: dict[str, dict] = {
    "low_light": {
        "soil_mix": "1 часть торфяной смеси + 1 часть перлита для дренажа",
        "fertiliser_npk": "сбалансированный 3-1-2 в половинной дозе",
        "watering_method": "soak_and_drain",
    },
    "medium_light": {
        "soil_mix": "2 части торфа или кокоса + 1 часть перлита + 1 часть коры",
        "fertiliser_npk": "сбалансированный 3-1-2 в половинной дозе",
        "watering_method": "soak_and_drain",
    },
    "bright_indirect": {
        "soil_mix": "2 части торфа или кокоса + 1 часть перлита + 1 часть коры",
        "fertiliser_npk": "сбалансированный 3-1-2 в полной дозе",
        "watering_method": "soak_and_drain",
    },
    "direct_sun": {
        "soil_mix": "быстродренируемая смесь для кактусов/суккулентов или 1:1 с крошкой",
        "fertiliser_npk": "пониженный N 2-3-3 для закалки",
        "watering_method": "soak_and_drain",
    },
}

# Checkpoint and season-note text per locale.
_CHECKPOINT_TEXT = {
    "en": {
        "spot_check_short": "Spot-check for new spores / spots / wilting.",
        "spot_check": "Spot-check for new symptoms.",
        "underside": "Inspect the underside of the leaves.",
        "rescan": "Repeat scan; expect signs of recovery.",
        "rescan_compare": "Repeat scan and compare progression.",
        "monthly_check": "Quick visual check; wipe dust.",
        "season_note": "Dormant season: reduce watering and pause feeding to prevent rot.",
    },
    "ru": {
        "spot_check_short": "Осмотрите листья: новые споры, пятна или увядание?",
        "spot_check": "Осмотрите на наличие новых симптомов.",
        "underside": "Проверьте нижнюю сторону листьев.",
        "rescan": "Повторите скан; ждите признаков улучшения.",
        "rescan_compare": "Повторите скан и сравните прогрессию.",
        "monthly_check": "Быстрый осмотр и удаление пыли.",
        "season_note": "Период покоя: уменьшите полив и пропустите подкормку, чтобы избежать гнили.",
    },
}


def _is_russian(locale: str | None) -> bool:
    return bool(locale and locale.lower().startswith("ru"))


def _localise(field: str, key: str, default, locale: str):
    """Return the Russian copy for (``key``, ``field``) when locale is
    Russian and the override exists; otherwise the English default.

    Used uniformly for disease-protocol overrides, light-tier text,
    and base-care strings so callers don't need to know which table
    holds which key.
    """
    if not _is_russian(locale):
        return default
    for table in (_DISEASE_PROTOCOL_RU, _LIGHT_TIER_PROFILE_RU, _BASE_CARE_TEXT_RU):
        if key in table and field in table[key]:
            return table[key][field]
    return default


def _current_growing_season(today: _dt.date | None = None) -> str:
    """Return ``"growing"`` or ``"dormant"`` for the supplied date.

    Northern-hemisphere default (Mar–Oct = growing). For southern-
    hemisphere users we could flip the table later, but the audience
    is overwhelmingly NH today and the season selector is exposed via
    the request so callers can override.
    """
    today = today or _dt.date.today()
    return "growing" if 3 <= today.month <= 10 else "dormant"


def _apply_season(plan: dict, season: str, locale: str = "en") -> dict:
    """Dampen feeding + watering in dormant months.

    In dormant months we stretch watering by ~40% (or skip if the
    plant is already on as-needed), pause fertiliser for the four
    classes that allow seasonal feeding, and surface the season hint
    so the LLM polish (and the client UI) can mention it.
    """
    if season != "dormant":
        plan["season"] = "growing"
        return plan
    plan["season"] = "dormant"
    wd = plan.get("watering_days")
    if isinstance(wd, int) and wd > 0:
        plan["watering_days"] = max(wd + max(3, wd // 3), 7)
    fw = plan.get("fertilise_weeks")
    if isinstance(fw, int) and fw > 0:
        # Cut fertiliser frequency in half during dormant months. A
        # value of 0 below means "skip fertilising entirely".
        plan["fertilise_weeks"] = max(fw * 2, 8)
    lang = "ru" if _is_russian(locale) else "en"
    plan.setdefault(
        "season_note", _CHECKPOINT_TEXT[lang]["season_note"],
    )
    return plan


_SUMMARY_COPY = {
    "en": {
        "water_as_needed": "Water only when the top 3 cm of soil is dry.",
        "water_every": "Water every **{n} days**; let the top 2 cm dry between waterings.",
        "water_fallback": "Water per the species' preference.",
        "feed_every": "Feed every **{n} weeks** with {npk}.",
        "feed_skip": "Skip fertilising for now — the plant is recovering.",
        "humidity": "Keep humidity in the {min}–{max}% range.",
        "temp": "Daytime **{day0}–{day1}°C**, night **{night0}–{night1}°C**.",
        "isolate": "**Isolate this plant** until the protocol completes.",
        "treatment_heading": "**Treatment**",
        "balanced_npk": "a balanced NPK",
    },
    "ru": {
        "water_as_needed": "Поливайте только когда верхние 3 см почвы просохли.",
        "water_every": "Полив раз в **{n} дн.**; верхние 2 см должны подсохнуть между поливами.",
        "water_fallback": "Полив по предпочтениям вида.",
        "feed_every": "Подкормка раз в **{n} нед.** препаратом {npk}.",
        "feed_skip": "Пропустите подкормку — растению нужно восстановление.",
        "humidity": "Поддерживайте влажность {min}–{max}%.",
        "temp": "Днём **{day0}–{day1}°C**, ночью **{night0}–{night1}°C**.",
        "isolate": "**Изолируйте растение** до окончания протокола.",
        "treatment_heading": "**Лечение**",
        "balanced_npk": "балансированным NPK",
    },
}


def _build_markdown_summary(plan: dict, locale: str) -> str:
    """Compose a deterministic Markdown summary from the rule output.

    Used as a graceful fallback when no OpenRouter key is configured —
    the client always gets a renderable plan, never a barebones JSON.
    The LLM polish, if it runs, replaces this string.
    """
    lang = "ru" if _is_russian(locale) else "en"
    copy = _SUMMARY_COPY[lang]

    wd = plan.get("watering_days")
    if wd == "as_needed":
        watering = copy["water_as_needed"]
    elif isinstance(wd, int):
        watering = copy["water_every"].format(n=wd)
    else:
        watering = copy["water_fallback"]
    fw = plan.get("fertilise_weeks")
    if isinstance(fw, int) and fw > 0:
        npk = plan.get("fertiliser_npk") or copy["balanced_npk"]
        feed = copy["feed_every"].format(n=fw, npk=npk)
    else:
        feed = copy["feed_skip"]
    band = plan.get("humidity_band") or {}
    humidity = (
        copy["humidity"].format(min=band.get("min", 40), max=band.get("max", 60))
        if band else ""
    )
    temp = plan.get("temperature_c") or {}
    if temp:
        day = temp.get("day") or [18, 26]
        night = temp.get("night") or [15, 22]
        temp_line = copy["temp"].format(
            day0=day[0], day1=day[1], night0=night[0], night1=night[1],
        )
    else:
        temp_line = ""
    season_note = plan.get("season_note", "")
    treatment_lines = "\n".join(
        f"- {step}" for step in (plan.get("treatment") or [])[:4]
    )
    bullets: list[str] = [
        f"- {watering}",
        f"- {feed}",
        f"- {humidity}" if humidity else "",
        f"- {temp_line}" if temp_line else "",
    ]
    if plan.get("isolation"):
        bullets.append(f"- {copy['isolate']}")
    md = "\n".join(b for b in bullets if b)
    if treatment_lines:
        md += f"\n\n{copy['treatment_heading']}\n{treatment_lines}"
    if season_note:
        md += f"\n\n_{season_note}_"
    return md


def _enforce_canonical_dosages(text: str, plan: dict) -> bool:
    """Verify that the LLM-polished summary does not contradict the canonical dosages.
    Returns True if valid, False if it violates the canonical numbers.
    """
    text_lower = text.lower()

    # 1. Check NPK: if the canonical NPK has digits (like "10-15-10"), the polished text
    # should not contain a different NPK ratio pattern.
    canonical_npk = str(plan.get("fertiliser_npk", "")).lower()
    npk_pattern = re.compile(r"\b\d{1,2}[-:\s]+\d{1,2}[-:\s]+\d{1,2}\b")
    canonical_npk_nums = re.findall(r"\d+", canonical_npk)

    for match in npk_pattern.finditer(text_lower):
        matched_str = match.group(0)
        matched_nums = re.findall(r"\d+", matched_str)
        if matched_nums != canonical_npk_nums:
            logger.warning(
                "LLM polish rejected: NPK ratio mismatch. Polished: %r, Canonical: %r",
                matched_str, canonical_npk
            )
            return False

    # Helper: reject only when an explicit *interval* (a number bound to a
    # time unit, e.g. "7 days" / "2 weeks" / "5 дн.") near a watering or
    # fertilising keyword contradicts the canonical cadence. Incidental
    # numbers that are not intervals — "let the top 2 cm dry", a "70%"
    # humidity band — must be ignored; otherwise the deterministic template
    # itself ("Water every 7 days; let the top 2 cm dry…") would be
    # rejected by its own unrelated "2 cm".
    def check_keyword_number(
        keywords: list[str], canonical_val: int, unit_pattern: str, label: str
    ) -> bool:
        interval_re = re.compile(rf"\b(\d+)\s*(?:{unit_pattern})")
        for kw in keywords:
            # Search for keyword and check interval numbers within 40 chars.
            for match in re.finditer(re.escape(kw), text_lower):
                start = max(0, match.start() - 40)
                end = min(len(text_lower), match.end() + 40)
                window = text_lower[start:end]
                for m in interval_re.finditer(window):
                    num = int(m.group(1))
                    # Ignore trivial values like "1 time" / "0 days".
                    if num in (0, 1):
                        continue
                    if num != canonical_val:
                        logger.warning(
                            "LLM polish rejected: %s mismatch in window %r around %r. Polished: %d, Canonical: %d",
                            label, window, kw, num, canonical_val
                        )
                        return False
        return True

    # 2. Check watering days (interval unit: days / дн.)
    watering_val = plan.get("watering_days")
    if isinstance(watering_val, int):
        watering_kws = ["water", "watering", "полив"]
        if not check_keyword_number(watering_kws, watering_val, r"days?|дн", "watering"):
            return False

    # 3. Check fertilising weeks (interval unit: weeks / нед.)
    fertilise_val = plan.get("fertilise_weeks")
    if isinstance(fertilise_val, int):
        fertilise_kws = ["fertiliz", "fertilise", "fertilising", "fertilizer", "удобр"]
        if not check_keyword_number(fertilise_kws, fertilise_val, r"weeks?|нед", "fertilise"):
            return False

    return True


# Care-plan LLM polish is deterministic in its input (the rule-engine plan +
# locale), so the ~10-20s OpenRouter rewrite is cached with the same long TTL
# and disk-persistence as species_common_diseases — otherwise every care-plan
# request re-paid for an identical prose summary.
CARE_PLAN_CACHE_SIZE = get_int("CARE_PLAN_CACHE_SIZE", 256)
CARE_PLAN_CACHE_TTL_SECONDS = get_int("CARE_PLAN_CACHE_TTL_SECONDS", 7 * 24 * 3600)
# Headroom so the polished summary isn't cut off mid-sentence by a reasoning
# model spending budget before it writes (see SPECIES_DISEASES_MAX_TOKENS).
CARE_PLAN_POLISH_MAX_TOKENS = get_int("CARE_PLAN_POLISH_MAX_TOKENS", 1024)
_CARE_PLAN_CACHE = _LRU(CARE_PLAN_CACHE_SIZE, CARE_PLAN_CACHE_TTL_SECONDS)
# ``_v2`` namespace abandons entries cached before the truncation fix: a
# reasoning model truncated the polish past the dosage guard, so the old dir
# held summaries cut off mid-sentence that would otherwise be served for the
# full 7-day TTL. The old ``care_plan_polish`` dir is now orphaned (safe to rm).
_CARE_PLAN_DISK = _DiskCache("care_plan_polish_v2", CARE_PLAN_CACHE_TTL_SECONDS)


def _polish_plan_with_llm(plan: dict, locale: str) -> dict:
    """Optional LLM polish step. Returns the plan with a friendly
    Markdown summary attached, or the input plan unchanged on failure.

    A deterministic fallback Markdown summary is always present (added
    upstream in ``generate_care_plan``); the LLM only replaces it.

    The polished summary is cached (in-memory + disk) keyed on the
    deterministic plan content, so an identical (disease, species, light,
    locale, season) tuple never re-pays for the OpenRouter rewrite.
    """
    if not _chat_api_key_present():
        return plan
    # Key on the plan content itself: it's deterministic in the care-plan
    # inputs and already carries the resolved ``season`` and ``summary_md``,
    # so this captures the full request shape without re-deriving it. The
    # incoming ``summary_md`` is the deterministic fallback we're about to
    # replace, so it's part of the stable key, not noise.
    cache_key = json.dumps(plan, sort_keys=True, ensure_ascii=False) + f"|{locale}"
    polished = _CARE_PLAN_CACHE.get(cache_key)
    if polished is None:
        polished = _CARE_PLAN_DISK.get(cache_key)
        if polished is not None:
            _CARE_PLAN_CACHE.put(cache_key, polished)
    if polished is not None:
        return {**plan, "summary_md": polished, "cached": True}
    locale_hint = (
        "Write the summary in Russian." if locale.lower().startswith("ru")
        else "Write the summary in English."
    )
    # Echo the canonical schedule numbers straight into the instructions.
    # Asking the model to merely "keep intervals as given" (buried in the
    # JSON blob) made it paraphrase-and-drift — "water every 10 days" when
    # the plan said 5 — and every such drift is rejected by
    # ``_enforce_canonical_dosages``, silently losing the polish. Restated
    # as explicit fill-in values the numbers survive the rewrite.
    canonical_bits: list[str] = []
    watering_days = plan.get("watering_days")
    if isinstance(watering_days, int) and watering_days > 0:
        canonical_bits.append(f"water every {watering_days} days")
    fertilise_weeks = plan.get("fertilise_weeks")
    # 0 means "do not fertilise" (e.g. viral / edema plans) — echoing
    # "every 0 weeks" would only invite the model to invent a cadence.
    if isinstance(fertilise_weeks, int) and fertilise_weeks > 0:
        canonical_bits.append(f"fertilise every {fertilise_weeks} weeks")
    npk = str(plan.get("fertiliser_npk") or "").strip()
    if npk:
        canonical_bits.append(f"fertiliser NPK {npk}")
    canonical_hint = (
        "The canonical schedule is: " + "; ".join(canonical_bits) + ". "
        "Copy these numbers verbatim — never recompute, round or replace "
        "them. " if canonical_bits else ""
    )
    system = (
        "You polish a plant care plan into 5-7 short Markdown bullet "
        "points covering watering, light, humidity, feeding and disease "
        "actions. " + canonical_hint
        + "Keep all other dosages and intervals exactly as given. "
        + locale_hint
    )
    user_blocks = [{"role": "user", "content": json.dumps(plan, ensure_ascii=False)}]
    try:
        polish, _, _ = _call_openrouter(
            system, user_blocks,
            max_tokens=CARE_PLAN_POLISH_MAX_TOKENS, require_complete=True,
            # Structured rewrite, not reasoning — pin the shortest trace so a
            # raised global effort can't truncate the polish (see species call).
            reasoning_effort="minimal",
        )
        if polish and _enforce_canonical_dosages(polish, plan):
            _CARE_PLAN_CACHE.put(cache_key, polish)
            _CARE_PLAN_DISK.put(cache_key, polish)
            plan = {**plan, "summary_md": polish}
    except Exception as e:  # noqa: BLE001
        logger.info("Care-plan polish skipped: %s", e)
    return plan


# ── Humidity-habit classifier (species → desert / tropical) ─────────
#
# Curated whole-word token sets. We list the *genus* of the plants we
# want to match plus a handful of unambiguous common-name keywords.
# Generic common names that often appear inside non-matching binomials
# ("fern" → "Asparagus fern", "aloe" → "Aloeswood Saraca") are
# deliberately absent: the genus-level cue carries the same signal
# without the false positives.

_SUCCULENT_TOKENS: frozenset[str] = frozenset({
    # Common names that are uniquely succulent.
    "cactus", "cacti", "succulent",
    # Canonical succulent / xeric genera.
    "aloe", "agave", "crassula", "echeveria", "haworthia", "kalanchoe",
    "sansevieria", "sedum", "sempervivum", "lithops", "graptopetalum",
    "pachyphytum", "yucca", "opuntia", "ferocactus", "mammillaria",
    "euphorbia",
})

_FERN_TROPICAL_TOKENS: frozenset[str] = frozenset({
    # True-fern genera. We do NOT include the bare word "fern" — many
    # common names ("Asparagus fern" = Asparagus aethiopicus,
    # Liliaceae) carry the word without belonging to Polypodiopsida.
    "nephrolepis", "polystichum", "adiantum", "pteris", "asplenium",
    "platycerium", "davallia", "blechnum", "cyrtomium",
    # High-humidity tropical-foliage genera ("jungle plant" group).
    "calathea", "maranta", "alocasia", "anthurium", "philodendron",
    "monstera", "ctenanthe", "stromanthe", "fittonia", "syngonium",
    "spathiphyllum",
})


def _humidity_habit(species: str | None) -> str | None:
    """Classify a species string as ``"succulent"``, ``"fern_tropical"``,
    or ``None`` (no recognised habit).

    Word-boundary tokenisation only — substring matching trips on
    "Asparagus fern" / "Aloeswood Saraca" and similar false friends.
    Both binomials ("Begonia rex") and common names ("Cactus") are
    handled the same way: we split on non-word characters, lowercase
    each token, and intersect against the curated sets above.
    """
    if not species:
        return None
    tokens = {t for t in re.split(r"\W+", species.lower()) if t}
    if tokens & _SUCCULENT_TOKENS:
        return "succulent"
    if tokens & _FERN_TROPICAL_TOKENS:
        return "fern_tropical"
    return None


def generate_care_plan(
    *,
    disease: str,
    species: str | None = None,
    light: str = "bright_indirect",
    locale: str = "en",
    season: str | None = None,
    today: _dt.date | None = None,
) -> dict:
    """Build a full plant care plan for the (disease, light) tuple.

    Returns a richer, production-grade payload than the v1 endpoint:

      * Backward-compat (v1 callers keep working): ``watering_days``,
        ``fertilise_weeks``, ``humidity_target``, ``key_action``,
        ``follow_up_days``.
      * New fields (additive — older clients ignore them):
        ``light_lux_target``, ``placement``, ``acclimation``,
        ``watering_method``, ``soil_mix``, ``fertiliser_npk``,
        ``humidity_band``, ``temperature_c``, ``treatment`` (list),
        ``sanitation`` (list), ``isolation`` (bool), ``red_flags``
        (list), ``checkpoints`` (list), ``season``, ``season_note``
        and a always-present ``summary_md`` Markdown summary.

    Season auto-detection uses today's date (northern-hemisphere
    default). Callers can override by passing ``season="growing"`` or
    ``"dormant"``; tests inject ``today`` for determinism.
    """
    light_key = light if light in _VALID_LIGHT_TIERS else "bright_indirect"
    light_profile = _LIGHT_TIER_PROFILE[light_key]
    base = _BASE_CARE[light_key]
    # Resolve the disease to its canonical protocol key once so the
    # English and Russian look-ups stay in lock-step. The endpoint
    # normalises unknown classes to ``"unknown"`` upstream, which
    # isn't in either protocol table; the EN code falls back to the
    # ``"healthy"`` protocol and the RU lookup must follow the same
    # fallback or it leaks English text on every unknown-class plan.
    protocol_key = disease if disease in _DISEASE_PROTOCOL else "healthy"
    protocol = _DISEASE_PROTOCOL[protocol_key]
    lang = "ru" if _is_russian(locale) else "en"

    humidity_band = protocol.get("humidity_band") or base["humidity_band"]
    humidity_target_str = f"{humidity_band['min']}-{humidity_band['max']}%"

    # Disease overrides win over base care for every key in `base`. We
    # merge explicitly so unknown protocol keys don't leak into the
    # response.
    merged_watering = protocol.get("watering_days", base["watering_days"])
    merged_method = protocol.get("watering_method", base["watering_method"])
    merged_fw = protocol.get("fertilise_weeks", base["fertilise_weeks"])

    isolation = bool(protocol.get("isolation", False))
    follow_up_days = int(protocol.get("follow_up_days", 14))

    # Checkpoints: when the user should re-check. We compose them
    # relative to the follow-up window so a plant on a 7-day cycle gets
    # day-3 + day-7 nudges and a healthy plant on a 30-day cycle gets
    # day-7 + day-30. Always at least two checkpoints.
    cp = _CHECKPOINT_TEXT[lang]
    checkpoints: list[dict] = []
    if follow_up_days <= 7:
        checkpoints = [
            {"day": 3, "check": cp["spot_check_short"]},
            {"day": follow_up_days, "check": cp["rescan"]},
        ]
    elif follow_up_days <= 14:
        checkpoints = [
            {"day": 3, "check": cp["underside"]},
            {"day": 7, "check": cp["spot_check"]},
            {"day": follow_up_days, "check": cp["rescan_compare"]},
        ]
    else:
        checkpoints = [
            {"day": 7, "check": cp["monthly_check"]},
            {"day": follow_up_days, "check": cp["rescan_compare"]},
        ]

    # Species-aware micro-tuning. When the auto-detected species
    # is a genus we recognise as desert / tropical / fern habit, we
    # nudge the humidity band so the plan reflects more than just
    # the disease + light tier. Matching is whole-word against the
    # genus token (first space-separated word from the species
    # string) — never a substring — to avoid the false positives the
    # naive `in` check produced. "Asparagus fern" used to match the
    # fern bucket on the substring "fern"; "Aloeswood Saraca" used
    # to match the succulent bucket on "aloe". Neither does now,
    # because we only check the genus / canonical common-name
    # tokens. Generic common-name fallbacks ("cactus", "succulent")
    # are accepted whole-word too — they don't appear inside any
    # known binomial we'd want NOT to match.
    if _humidity_habit(species) == "succulent":
        humidity_band = {"min": max(20, humidity_band["min"] - 15),
                         "max": max(35, humidity_band["max"] - 20)}
    elif _humidity_habit(species) == "fern_tropical":
        humidity_band = {"min": max(humidity_band["min"], 60),
                         "max": max(humidity_band["max"], 80)}
    humidity_target_str = f"{humidity_band['min']}-{humidity_band['max']}%"

    placement = _localise("placement", light_key, light_profile["placement"], locale)
    acclimation = _localise("acclimation", light_key, light_profile["acclimation"], locale)
    soil_mix = _localise("soil_mix", light_key, base["soil_mix"], locale)
    # ``fertiliser_npk`` is unusual: the canonical English value can
    # come from either the disease protocol (e.g. nutrient_deficiency
    # overrides it) OR the light-tier base table. ``_localise`` keys
    # by a single string, so a plain call against `disease` misses
    # the base-tier RU strings for diseases that don't override the
    # field (powdery_mildew, healthy, etc.). Walk both tables in the
    # same order the English fallback does.
    fert_npk_en = protocol.get("fertiliser_npk", base["fertiliser_npk"])
    if _is_russian(locale):
        fert_npk = (
            _DISEASE_PROTOCOL_RU.get(protocol_key, {}).get("fertiliser_npk")
            or _BASE_CARE_TEXT_RU.get(light_key, {}).get("fertiliser_npk")
            or fert_npk_en
        )
    else:
        fert_npk = fert_npk_en
    key_action = _localise(
        "key_action", protocol_key,
        protocol.get("key_action", "Monitor weekly."),
        locale,
    )
    treatment = _localise(
        "treatment", protocol_key, list(protocol.get("treatment") or []), locale,
    )
    sanitation = _localise(
        "sanitation", protocol_key, list(protocol.get("sanitation") or []), locale,
    )
    red_flags = _localise(
        "red_flags", protocol_key, list(protocol.get("red_flags") or []), locale,
    )

    plan: dict = {
        # ── v1 contract (unchanged) ────────────────────────────────
        "species": species or "unknown",
        "disease": disease,
        "light_tier": light_key,
        "watering_days": merged_watering,
        "fertilise_weeks": merged_fw,
        "humidity_target": humidity_target_str,
        "key_action": key_action,
        "follow_up_days": follow_up_days,
        # ── v2 extensions (additive) ───────────────────────────────
        "light_lux_target": light_profile["lux_target"],
        "placement": placement,
        "acclimation": acclimation,
        "watering_method": merged_method,
        "soil_mix": soil_mix,
        "fertiliser_npk": fert_npk,
        "humidity_band": humidity_band,
        "temperature_c": base["temperature_c"],
        "treatment": list(treatment),
        "sanitation": list(sanitation),
        "isolation": isolation,
        "red_flags": list(red_flags),
        "checkpoints": checkpoints,
    }

    season_key = season if season in ("growing", "dormant") else _current_growing_season(today)
    plan = _apply_season(plan, season_key, locale=locale)
    plan["summary_md"] = _build_markdown_summary(plan, locale=locale)
    return _polish_plan_with_llm(plan, locale=locale)


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
