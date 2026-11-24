"""Care plan: the deterministic, rules-based plan and its optional LLM polish.

``generate_care_plan`` always returns a usable plan from the rule tables in
``care_plan_content`` (no key needed); when OpenRouter is configured it can have
the summary rewritten, guarded by the canonical-dosage check and cached in memory
and on disk. The transport (``_call_openrouter``), the shared prompt prefix, the
cache classes and the key check stay in ``ai_services`` and are reached as
``ai_services.name`` at call time, so a test that patches one there is seen here.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading

try:
    from . import ai_services  # type: ignore[import-not-found]
    from .care_plan_content import (  # type: ignore[import-not-found]
        _BASE_CARE,
        _BASE_CARE_TEXT_RU,
        _CHECKPOINT_TEXT,
        _DISEASE_PROTOCOL,
        _DISEASE_PROTOCOL_RU,
        _LIGHT_TIER_PROFILE,
        _LIGHT_TIER_PROFILE_RU,
        _SUMMARY_COPY,
        _VALID_LIGHT_TIERS,
    )
    from .config import get_int, get_str  # type: ignore[import-not-found]
    from .diseases_data import (  # type: ignore[import-not-found]
        TREATMENT_CONDITIONS,
        TREATMENT_PRODUCTS,
    )
except ImportError:
    import ai_services
    from care_plan_content import (
        _BASE_CARE,
        _BASE_CARE_TEXT_RU,
        _CHECKPOINT_TEXT,
        _DISEASE_PROTOCOL,
        _DISEASE_PROTOCOL_RU,
        _LIGHT_TIER_PROFILE,
        _LIGHT_TIER_PROFILE_RU,
        _SUMMARY_COPY,
        _VALID_LIGHT_TIERS,
    )
    from config import get_int, get_str
    from diseases_data import TREATMENT_CONDITIONS, TREATMENT_PRODUCTS

logger = logging.getLogger(__name__)


# ── Care plan ───────────────────────────────────────────────────────
# The rule tables live in care_plan_content.py (see its header).

def _localise(field: str, key: str, default, locale: str):
    """Return the Russian copy for (``key``, ``field``) when locale is
    Russian and the override exists; otherwise the English default.

    Used uniformly for disease-protocol overrides, light-tier text,
    and base-care strings so callers don't need to know which table
    holds which key.
    """
    if not ai_services._is_russian(locale):
        return default
    for table in (_DISEASE_PROTOCOL_RU, _LIGHT_TIER_PROFILE_RU, _BASE_CARE_TEXT_RU):
        if key in table and field in table[key]:
            return table[key][field]
    return default


def _localised_products(protocol_key: str, locale: str) -> list[dict] | None:
    """Curated product catalogue entry for [protocol_key], localised —
    see ``diseases_data.TREATMENT_PRODUCTS`` for why this is hand-written
    rather than generated. ``None`` when the class has no curated entry;
    the caller must omit the "products" key entirely in that case rather
    than send an empty list."""
    entries = TREATMENT_PRODUCTS.get(protocol_key)
    if not entries:
        return None
    lang = "ru" if ai_services._is_russian(locale) else "en"
    return [
        {
            "key": entry["key"],
            "kind": entry["kind"],
            "name": entry[f"name_{lang}"],
            "active_ingredient": entry[f"active_ingredient_{lang}"],
            "note": entry.get(f"note_{lang}"),
        }
        for entry in entries
    ]


def _localised_treatment_conditions(protocol_key: str, locale: str) -> dict | None:
    """Application-window guidance for [protocol_key], localised. ``None``
    when there's no curated entry (mirrors [_localised_products])."""
    entry = TREATMENT_CONDITIONS.get(protocol_key)
    if not entry:
        return None
    lang = "ru" if ai_services._is_russian(locale) else "en"
    return {
        "best_time": entry[f"best_time_{lang}"],
        "temp_c": entry["temp_c"],
        "dry_hours": entry["dry_hours"],
    }


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
    lang = "ru" if ai_services._is_russian(locale) else "en"
    plan.setdefault(
        "season_note", _CHECKPOINT_TEXT[lang]["season_note"],
    )
    return plan


def _build_markdown_summary(plan: dict, locale: str) -> str:
    """Compose a deterministic Markdown summary from the rule output.

    Used as a graceful fallback when no OpenRouter key is configured —
    the client always gets a renderable plan, never a barebones JSON.
    The LLM polish, if it runs, replaces this string.
    """
    lang = "ru" if ai_services._is_russian(locale) else "en"
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
            logger.warning("LLM polish rejected for NPK ratio mismatch")
            return False

    # Helper: reject only when an explicit *interval* (a number bound to a
    # time unit, e.g. "7 days" / "2 weeks" / "5 дн.") near a watering or
    # fertilising keyword contradicts the canonical cadence. Incidental
    # numbers that are not intervals — "let the top 2 cm dry", a "70%"
    # humidity band — must be ignored; otherwise the deterministic template
    # itself ("Water every 7 days; let the top 2 cm dry…") would be
    # rejected by its own unrelated "2 cm".
    def check_keyword_number(
        keywords: list[str], canonical_val: int, unit_pattern: str
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
                        logger.warning("LLM polish rejected for dosage mismatch")
                        return False
        return True

    # 2. Check watering days (interval unit: days / дн.)
    watering_val = plan.get("watering_days")
    if isinstance(watering_val, int):
        watering_kws = ["water", "watering", "полив"]
        if not check_keyword_number(watering_kws, watering_val, r"days?|дн"):
            return False

    # 3. Check fertilising weeks (interval unit: weeks / нед.)
    fertilise_val = plan.get("fertilise_weeks")
    if isinstance(fertilise_val, int):
        fertilise_kws = ["fertiliz", "fertilise", "fertilising", "fertilizer", "удобр"]
        if not check_keyword_number(fertilise_kws, fertilise_val, r"weeks?|нед"):
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
CARE_PLAN_POLISH_MAX_TOKENS = get_int("CARE_PLAN_POLISH_MAX_TOKENS", 3072)
# Medium effort: the rewrite is bound by hard numeric constraints (canonical
# dosages that must survive verbatim) and _enforce_canonical_dosages throws the
# whole polish away when one drifts — so thinking here buys constraint
# adherence, which is exactly the quality metric this call is judged on.
CARE_PLAN_POLISH_REASONING_EFFORT = get_str(
    "CARE_PLAN_POLISH_REASONING_EFFORT", "medium"
).strip().lower()
_CARE_PLAN_CACHE = ai_services._LRU(CARE_PLAN_CACHE_SIZE, CARE_PLAN_CACHE_TTL_SECONDS)
# The namespace is bumped whenever cached summaries stop matching what the
# current prompt would produce — otherwise the old text is served for the full
# 7-day TTL and the change appears to have done nothing. ``_v2`` abandoned
# summaries truncated mid-sentence by the old model; ``_v3`` abandons summaries
# written before the prompt started including the follow-up checkpoint and red
# flags, which is exactly the content this cache would have hidden. Earlier
# dirs are orphaned and safe to rm.
_CARE_PLAN_DISK = ai_services._DiskCache("care_plan_polish_v3", CARE_PLAN_CACHE_TTL_SECONDS)

# The static half of the polish instructions. Naming `checkpoints` and
# `red_flags` explicitly is the single biggest content gain in this call: the
# older wording asked only for "watering, light, humidity, feeding and disease
# actions", so the rewrite silently DROPPED the two fields the deterministic
# summary carries and the owner actually acts on — when to check back, and what
# means "stop and escalate". "Generic active ingredients only" and "never
# introduce a number of your own" are in _HOUSE_PREAMBLE; they are restated
# here only where this task makes them sharper.
_CARE_PLAN_POLISH_TASK = """## Your task

Rewrite the plant care plan in the user message - it arrives as a JSON object - into 5-7
short Markdown bullets for the plant's owner, in this order: the disease action first,
then watering, light and placement, humidity, feeding, and last the next check-up from
`checkpoints`. When `red_flags` is non-empty, fold the single most serious one into that
last bullet as the sign to escalate.

Use ONLY facts present in the JSON: copy every interval, dosage, NPK ratio, temperature
and percentage exactly as written. Output the bullets and nothing else - no heading, no
intro line, no closing remark."""

# Polish outcome counters for /api/metrics. A polish that drifts off the
# canonical dosages is dropped SILENTLY (the user still gets the deterministic
# summary), so without a counter a model or prompt change that starts failing
# the dosage guard on every call looks exactly like everything working. This is
# the one direct quality signal we have on care-plan prose: watch `rejected`
# against `accepted`.
_care_plan_totals = {"attempts": 0, "accepted": 0, "rejected": 0, "failed": 0}
_care_plan_lock = threading.Lock()


def care_plan_metrics_snapshot() -> dict:
    """Read-only snapshot of care-plan polish outcomes for /api/metrics.

    ``rejected`` counts polishes the model returned but the canonical-dosage
    guard refused (the plan's own numbers were contradicted); ``failed`` counts
    calls that never produced text at all (API error / truncation). Both fall
    back to the deterministic summary, so neither is user-visible directly.
    """
    with _care_plan_lock:
        attempts = _care_plan_totals["attempts"]
        rejected = _care_plan_totals["rejected"]
        return {
            **_care_plan_totals,
            "reject_ratio": round(rejected / attempts, 4) if attempts else 0.0,
        }


def _polish_plan_with_llm(plan: dict, locale: str, *, cached_only: bool = False) -> dict:
    """Optional LLM polish step. Returns the plan with a friendly
    Markdown summary attached, or the input plan unchanged on failure.

    A deterministic fallback Markdown summary is always present (added
    upstream in ``generate_care_plan``); the LLM only replaces it.

    The polished summary is cached (in-memory + disk) keyed on the
    deterministic plan content, so an identical (disease, species, light,
    locale, season) tuple never re-pays for the OpenRouter rewrite.
    """
    if not ai_services._chat_api_key_present():
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
    if cached_only:
        # The caller wants only what costs nothing; it charges the user's
        # allowance before asking again without this flag.
        return plan
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
        "The canonical schedule for THIS plan is: " + "; ".join(canonical_bits)
        + ". Copy these numbers verbatim - never recompute, round or replace them."
        if canonical_bits else ""
    )
    # Composition order matters twice over. `_system_prompt` puts the shared
    # house preamble and the static task block first so the ~1600-token prefix
    # is byte-identical for every care plan and caches; the two hints that
    # differ per plan (this plan's intervals, this request's language) go last,
    # where they cost their own tokens and nothing else's. Splicing
    # `canonical_hint` into the MIDDLE of the task text, as this used to, put a
    # per-plan value ahead of every stable rule behind it — a guaranteed miss.
    system = ai_services._system_prompt(_CARE_PLAN_POLISH_TASK, canonical_hint, locale_hint)
    user_blocks = [{"role": "user", "content": json.dumps(plan, ensure_ascii=False)}]
    with _care_plan_lock:
        _care_plan_totals["attempts"] += 1
    try:
        # Also uncached upstream: short prompt, and identical plans are already
        # served from _CARE_PLAN_CACHE / _CARE_PLAN_DISK before we get here.
        polish, _, _, _ = ai_services._call_openrouter(
            system, user_blocks,
            max_tokens=CARE_PLAN_POLISH_MAX_TOKENS, require_complete=True,
            reasoning_effort=CARE_PLAN_POLISH_REASONING_EFFORT,
        )
        if polish and _enforce_canonical_dosages(polish, plan):
            _CARE_PLAN_CACHE.put(cache_key, polish)
            _CARE_PLAN_DISK.put(cache_key, polish)
            plan = {**plan, "summary_md": polish}
            with _care_plan_lock:
                _care_plan_totals["accepted"] += 1
        else:
            with _care_plan_lock:
                _care_plan_totals["rejected"] += 1
    except Exception:  # noqa: BLE001 - provider response details stay private
        with _care_plan_lock:
            _care_plan_totals["failed"] += 1
        logger.info("Care-plan polish skipped")
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


@ai_services._plain_punctuation_result
def generate_care_plan(
    *,
    disease: str,
    species: str | None = None,
    light: str = "bright_indirect",
    locale: str = "en",
    season: str | None = None,
    today: _dt.date | None = None,
    polish: bool = True,
    cached_polish_only: bool = False,
) -> dict:
    """Build a full plant care plan for the (disease, light) tuple.

    ``polish=False`` skips the LLM summary and returns the rules-based
    ``summary_md`` — the server passes it once a caller has used up its daily
    allowance of paid calls, so the plan itself is never refused.
    ``cached_polish_only=True`` attaches a previously polished summary when one
    is cached (``cached: True``) and otherwise stops before the LLM, so the
    server can serve free results without spending a daily allowance.

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
    lang = "ru" if ai_services._is_russian(locale) else "en"

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
    if ai_services._is_russian(locale):
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
    products = _localised_products(protocol_key, locale)
    treatment_conditions = _localised_treatment_conditions(protocol_key, locale)

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
    # Both omitted entirely (not an empty list/dict) when the protocol has
    # no curated entry — additive v2 fields, older clients ignore unknown
    # keys either way, but an empty "products": [] would read as "checked,
    # nothing found" rather than "not curated yet".
    if products:
        plan["products"] = products
    if treatment_conditions:
        plan["treatment_conditions"] = treatment_conditions

    season_key = season if season in ("growing", "dormant") else _current_growing_season(today)
    plan = _apply_season(plan, season_key, locale=locale)
    plan["summary_md"] = _build_markdown_summary(plan, locale=locale)
    if not polish:
        return plan
    return _polish_plan_with_llm(plan, locale=locale, cached_only=cached_polish_only)
