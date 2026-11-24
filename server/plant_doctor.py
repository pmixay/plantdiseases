"""AI Plant Doctor: the chat sheet's per-session conversation over OpenRouter.

Owns the session map, its turn/token/message caps and the doctor's counters; the
transport (``_call_openrouter``), the shared prompt prefix and the key check stay
in ``ai_services`` and are reached as ``ai_services.name`` at call time, so a
test that patches one there is seen here.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

try:
    from . import ai_services  # type: ignore[import-not-found]
    from .config import get_int  # type: ignore[import-not-found]
except ImportError:
    import ai_services
    from config import get_int

logger = logging.getLogger(__name__)

# ── Configuration (config.json + env fallback) ──────────────────────


CHAT_SESSION_TURN_CAP = get_int("CHAT_SESSION_TURN_CAP", 20)
# Reasoning tokens count in completion_tokens, so they draw down this budget
# too. At 8000 a medium-effort conversation hit the cap after ~5 turns and the
# doctor went silent well before the 20-turn cap that is meant to be the
# binding limit. 32000 keeps turn_count the thing that ends a session (20 turns
# of this size is still well under a cent on luna pricing).
CHAT_SESSION_TOKEN_CAP = get_int("CHAT_SESSION_TOKEN_CAP", 32000)
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


# The chat doctor's own task block. Everything it used to restate — the
# taxonomy, the scope and injection rules, "generic active ingredients only" —
# now lives once in _HOUSE_PREAMBLE above.
_DOCTOR_TASK = """## Your task

You are the app's AI Plant Doctor, answering the owner's question in a chat sheet.

- 3-6 sentences. Specific, actionable, friendly.
- Ground the answer in the taxonomy above and in the classifier verdict the owner already
  has (supplied below when the app has one).
- If the owner describes edema or sunburn, ask about the watering, light and fertiliser
  routine, advise from first principles, and say plainly that the model has not diagnosed
  it.
- For a symptom outside the taxonomy entirely, say so, and suggest a photo scan or a local
  nursery.
- Cite the care guide with a token like [[doc:leaf_spot]] immediately after the
  condition's name. At most one per reply, only where it is relevant, double square
  brackets exactly as shown, and only a key from the care-guide list above or `healthy`."""


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
        """Trim message history for the next request — send at most the last N.

        Trimming happens in BLOCKS, not one message at a time. A plain
        ``messages[-keep_last:]`` window slides forward on every turn, so the
        moment a session outgrows ``keep_last`` the request prefix changes
        every single turn and the provider's prompt cache misses forever after
        — exactly when the history is longest and caching would pay the most.
        Dropping a block at a time instead holds the prefix byte-identical for
        several consecutive turns, then re-pays the cache write once.

        The block is even so the surviving window still opens on a "user"
        message, and the result never exceeds ``keep_last``.
        """
        n = len(self.messages)
        if n <= keep_last:
            return list(self.messages)
        block = max(2, keep_last // 2)
        block -= block % 2
        drop = -(-(n - keep_last) // block) * block   # round up to a whole block
        return self.messages[drop:]

    def append(self, role: str, content: str) -> None:
        """Append a message and enforce CHAT_SESSION_MAX_MESSAGES."""
        self.messages.append({"role": role, "content": content})
        excess = len(self.messages) - CHAT_SESSION_MAX_MESSAGES
        if excess > 0:
            del self.messages[:excess]


_chat_sessions: dict[str, _ChatSession] = {}
_chat_lock = threading.Lock()
# Server-wide totals for the abuse log / Prometheus.
_chat_totals = {
    "requests": 0, "rejected": 0, "tokens_in": 0, "tokens_out": 0,
    # Prompt-cache accounting. Both are subsets of tokens_in: cached tokens
    # were read from the provider's cache (billed at a fraction of input
    # price), cache_write tokens were stored into it (billed at or slightly
    # above input price, once, and amortised by later reads).
    "cached_tokens_in": 0, "cache_write_tokens_in": 0,
}


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
    """Compose the doctor's system prompt: house preamble + doctor task +
    locale hint + optional structured diagnosis context. Plain string so
    OpenRouter receives one clean system message.

    The two volatile parts trail the two stable ones deliberately — the
    diagnosis blob changes the moment the owner scans a different plant, and
    ahead of the preamble it would cost a full-price re-read of every cached
    token behind it (see ``_system_prompt``).
    """
    locale_hint = (
        "Reply in Russian." if locale.lower().startswith("ru")
        else "Reply in English."
    )
    diagnosis = ""
    if diagnosis_context:
        compact = _sanitize_diagnosis_context(diagnosis_context)
        diagnosis = "Latest classifier output:\n" + json.dumps(compact, ensure_ascii=False)
    return ai_services._system_prompt(_DOCTOR_TASK, locale_hint, diagnosis)


@ai_services._plain_punctuation_result
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

    if not ai_services._chat_api_key_present():
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
        # The surface where prompt caching pays MOST: the system prompt plus a
        # growing history is re-sent verbatim every turn, so each turn reads
        # back what the last one wrote. `session_id` keeps the conversation
        # pinned to the provider holding that cache, and `cache_prefix` asks
        # OpenRouter to advance the breakpoint as the history grows.
        reply_text, tin, tout, cache_stats = ai_services._call_openrouter(
            system_text, user_blocks,
            session_id=session_id, cache_prefix=True,
        )
    except Exception:  # noqa: BLE001 - prompt/provider details stay out of logs
        logger.warning("OpenRouter chat failed")
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
        # Subsets of tokens_in, tracked separately so /api/metrics can show
        # what share of input is actually being served from cache — the only
        # way to tell whether prompt caching is working against a given model.
        _chat_totals["cached_tokens_in"] += cache_stats["cached_tokens"]
        _chat_totals["cache_write_tokens_in"] += cache_stats["cache_write_tokens"]
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
        "model": ai_services.OPENROUTER_MODEL,
        "provider": "openrouter",
        # cached_input_tokens is a SUBSET of input_tokens (the part the
        # provider served from its prompt cache), not an addition to it.
        "usage": {
            "input_tokens": tin,
            "output_tokens": tout,
            "cached_input_tokens": cache_stats["cached_tokens"],
        },
        "session": {
            "session_id": session_id,
            "turns_used": turns_used,
            "turns_remaining": budget_left_turns,
            "tokens_used": tokens_used,
            "tokens_remaining": budget_left_tokens,
        },
    }


def chat_metrics_snapshot() -> dict:
    """Read-only snapshot of cross-session chat counters for /api/metrics.

    ``cached_tokens_in`` / ``cache_write_tokens_in`` are subsets of
    ``tokens_in``; ``cache_hit_ratio`` is the share of input tokens served from
    the provider's prompt cache. These count the DOCTOR only — see
    ``prompt_cache_metrics_snapshot`` for the same figures across every
    OpenRouter caller, which is the number to read when asking whether caching
    is engaging at all.
    """
    with _chat_lock:
        tokens_in = _chat_totals["tokens_in"]
        cached_in = _chat_totals["cached_tokens_in"]
        return {
            "active_sessions": len(_chat_sessions),
            "requests": _chat_totals["requests"],
            "rejected": _chat_totals["rejected"],
            "tokens_in": tokens_in,
            "tokens_out": _chat_totals["tokens_out"],
            "prompt_caching": ai_services.OPENROUTER_PROMPT_CACHING,
            "cached_tokens_in": cached_in,
            "cache_write_tokens_in": _chat_totals["cache_write_tokens_in"],
            "cache_hit_ratio": round(cached_in / tokens_in, 4) if tokens_in else 0.0,
        }
