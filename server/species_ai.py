"""Species screens' AI answers: common diseases and the bilingual species profile.

Both are best-effort OpenRouter calls that return ``{"available": False}`` with no
key or on any failure, cached in memory and on disk per species. Each keeps its own
caches, config and parsers here; the transport (``_call_openrouter``), the shared
prompt prefix, the cache classes and the key check stay in ``ai_services`` and are
reached as ``ai_services.name`` at call time, so a test that patches one there is
seen here.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

try:
    from . import ai_services  # type: ignore[import-not-found]
    from .config import get_int, get_str  # type: ignore[import-not-found]
except ImportError:
    import ai_services
    from config import get_int, get_str

logger = logging.getLogger(__name__)


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
# Headroom for the answer even when the model spends real budget reasoning
# (max_tokens is a cap, not a target — short answers stop early and only bill
# what they generate, so a higher cap costs nothing but avoids the truncation
# that returned an empty list and re-paid the call on every open). Doubled
# again alongside the move to medium effort: the trace bills against this cap.
SPECIES_DISEASES_MAX_TOKENS = get_int("SPECIES_DISEASES_MAX_TOKENS", 4096)
# Medium effort: this is factual recall about a specific species, and the
# failure mode is a plausible-but-wrong pest list cached for 7 days, not a
# clumsy sentence. Cached in memory + on disk per (species, language), so the
# effort is paid once per species and reused by every later request.
SPECIES_DISEASES_REASONING_EFFORT = get_str(
    "SPECIES_DISEASES_REASONING_EFFORT", "medium"
).strip().lower()
_SPECIES_DISEASES_CACHE = ai_services._LRU(SPECIES_DISEASES_CACHE_SIZE, SPECIES_DISEASES_CACHE_TTL_SECONDS)
# Persistent layer behind the LRU: the species→diseases LLM answer is stable,
# so once paid for it's reused across restarts and fresh installs instead of
# re-running the ~10-20s OpenRouter call every time.
# v2: the key set below widened, so answers cached under v1 carry doc_keys
# chosen from the old, too-narrow list (root rot -> nutrient_deficiency).
_SPECIES_DISEASES_DISK = ai_services._DiskCache("species_diseases_v2", SPECIES_DISEASES_CACHE_TTL_SECONDS)

# Disease-guide keys the Android client can deep-link (mirrors the app's
# DiseaseCatalog). The model may tag an item with one of these; anything
# else becomes null so the client shows the item as plain text.
# These are the app's GUIDE-DOC ids, not classifier class keys — the offline
# guide documents problems the model never predicts (root rot, blight, rust,
# spider mites…). Restricting this to the nine classes made the model tag
# "Root rot" with the closest key it had been given, nutrient_deficiency, and
# the client then deep-linked the wrong article. ``gray_mold_botrytis`` stays
# as the one class-key alias the client still maps (-> botrytis), so cached
# answers and the other surfaces keep resolving.
_GUIDE_DOC_KEYS = frozenset({
    "aphids", "blight", "botrytis", "edema", "gray_mold_botrytis", "leaf_mold",
    "leaf_scorch_underwatering", "leaf_spot", "mealybugs", "nutrient_deficiency",
    "pests", "powdery_mildew", "root_rot", "rust", "scale_insects",
    "spider_mites", "sunburn_or_scorch", "viral_disease",
})

# The response SHAPE is enforced by the JSON schema below, so the task block only
# carries what a schema can't: what makes an item correct and useful. Restating
# the RULES in prose is exactly the redundancy that costs gpt-5.6 quality — but
# the closing one-liner naming the keys stays, because it is the only contract
# left if a model/provider rejects the schema and `_call_openrouter` retries
# without it. Without it that fallback returns prose and the feature goes dark.
# "prefer the boring problem", "generic active ingredients only" and the
# care-guide key list are all in _HOUSE_PREAMBLE now — this block is composed
# behind it by `_system_prompt`, never sent on its own.
_SPECIES_DISEASES_TASK = """## Your task

List the 3-5 problems - diseases, sap-sucking pests, physiological disorders - the given \
species is most commonly affected by when grown INDOORS, most common first.

- Only conditions genuinely plausible for that species indoors.
- "name": the condition as an owner would recognise it.
- "note": one sentence under 20 words - how it shows up ON THIS SPECIES (the tissue and \
the sign), then the single most useful first action.
- "doc_key": the care-guide key naming the SAME condition, or null when none names it.
- Write "name" and "note" in the language the request asks for.

Answer with the JSON object {"diseases": [{"name", "doc_key", "note"}]} and nothing else."""

# Field descriptions are part of the contract the model sees — OpenRouter
# forwards them with the schema, so they do the job the removed prose rules
# used to do, at the point where the model is actually filling the field.
_SPECIES_DISEASES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["diseases"],
    "properties": {
        "diseases": {
            "type": "array",
            "description": "3 to 5 problems, most common first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "doc_key", "note"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short condition name, in the requested language.",
                    },
                    "doc_key": {
                        # Nullable enum, in the shape strict mode documents:
                        # null in `type`, never as an enum member. Without the
                        # null branch the schema would force one of these keys
                        # onto a condition the app's guide doesn't cover.
                        "type": ["string", "null"],
                        "enum": sorted(_GUIDE_DOC_KEYS),
                        "description": "Closest app guide key, or null if none fits.",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "One sentence, under 20 words, in the requested language: "
                            "how it appears on this species, then the first action."
                        ),
                    },
                },
            },
        },
    },
}


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


@ai_services._plain_punctuation_result
def species_common_diseases(
    scientific_name: str | None,
    *,
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
    lang = "ru" if ai_services._is_russian(lang) else "en"
    if not ai_services._chat_api_key_present():
        return {"available": False, "diseases": [], "source": "no_api_key"}

    # ``v3`` = this generation of the answer. Bumping the prefix retires every
    # entry the previous one wrote: v2 answers were built from a prompt that
    # also carried the caller's unkeyed common name (see ``_prompt_subject``),
    # so any of them could have been shaped by one client's input. Superseded
    # entries are never read again; their JSON files are harmless and can be
    # deleted.
    cache_key = f"v3:{name.lower()}:{lang}"
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
    subject = ai_services._prompt_subject(name)
    user = f"Species: {subject}\nLanguage for name and note: {lang_word}."
    try:
        # No cache_prefix/session_id here — those two are for a growing
        # conversation. This is a one-shot call, and what makes it cache is
        # simply that `_system_prompt` puts the shared house preamble in front
        # of it: every species-diseases request has a byte-identical ~1600-token
        # prefix (preamble + this task block), so only the trailing "Species:"
        # line is billed at full price. `prompt_cache_key` (set in
        # `_call_openrouter`) is what routes them to the same warm cache.
        text, _tin, _tout, _cache = ai_services._call_openrouter(
            ai_services._system_prompt(_SPECIES_DISEASES_TASK),
            [{"role": "user", "content": user}],
            max_tokens=SPECIES_DISEASES_MAX_TOKENS,
            require_complete=True,
            reasoning_effort=SPECIES_DISEASES_REASONING_EFFORT,
            json_schema=_SPECIES_DISEASES_SCHEMA,
            schema_name="species_diseases",
        )
    except Exception:  # noqa: BLE001 — never propagate provider details
        logger.warning("Species common-diseases lookup failed")
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


# ── Species profile (OpenRouter, structured) ────────────────────────
#
# Powers the species-detail screen's "flowering" + "native range" sections.
# Same discipline as ``species_common_diseases`` above: one OpenRouter call
# per species, strict JSON parsing with a tolerant-but-strict sanitiser,
# in-memory + disk cache, and a graceful ``available: False`` with no API key
# or on any failure. The canonical bilingual profile contains the same geography and
# numeric flowering data for every locale, plus both EN/RU copies of the short
# narrative strings. This prevents a language switch from changing the map.

SPECIES_PROFILE_CACHE_SIZE = get_int("SPECIES_PROFILE_CACHE_SIZE", 256)
SPECIES_PROFILE_CACHE_TTL_SECONDS = get_int("SPECIES_PROFILE_CACHE_TTL_SECONDS", 30 * 24 * 3600)
# Same headroom rationale as SPECIES_DISEASES_MAX_TOKENS: the hidden reasoning
# trace bills against max_tokens, so a generous cap avoids truncation even
# though the bilingual JSON answer itself is only a few hundred tokens.
SPECIES_PROFILE_MAX_TOKENS = get_int("SPECIES_PROFILE_MAX_TOKENS", 4096)
# The one call in this module that earns MORE than the medium floor. It is the
# hardest recall task here — a 12-month bloom calendar plus ISO-3166 native
# range for an arbitrary binomial — it is cached for 30 days per species (a
# wrong country sits on the map for a month), and it is the only output the
# user sees as data rather than prose, where a confident mistake is obvious.
# One call per species ever, so the extra thinking is paid once.
SPECIES_PROFILE_REASONING_EFFORT = get_str(
    "SPECIES_PROFILE_REASONING_EFFORT", "high"
).strip().lower()
_SPECIES_PROFILE_CACHE = ai_services._LRU(SPECIES_PROFILE_CACHE_SIZE, SPECIES_PROFILE_CACHE_TTL_SECONDS)
_SPECIES_PROFILE_DISK = ai_services._DiskCache("species_profile", SPECIES_PROFILE_CACHE_TTL_SECONDS)

_NATIVE_RANGE_STATUSES = frozenset({"grows_more", "grows_less"})

# As with _SPECIES_DISEASES_TASK: the schema below carries the shape, so this
# block carries only botany — the rules a schema cannot express — plus the
# closing shape one-liner that keeps the no-schema fallback path parseable.
# "describe the genus' pattern rather than inventing a species-specific claim"
# moved into _HOUSE_PREAMBLE; this block is composed behind it by
# `_system_prompt`, never sent on its own.
_SPECIES_PROFILE_TASK = """## Your task

Describe the given species' flowering pattern and native range from its known botany.

- "monthly_intensity": the species' normal flowering season under SUITABLE CULTIVATION, in \
the northern hemisphere - EXACTLY 12 values, one per calendar month starting with January. Do \
NOT zero the calendar just because flowering indoors is uncommon - put that limitation in \
"indoor_note". All zeros only for a species that does not flower biologically, such as a fern.
- "zones": 3 to 6 UNIQUE countries where the species is native, naturalised or widely \
cultivated outdoors - a country, never a region or continent. "grows_more" = thrives there \
(native range or common outdoor cultivation); "grows_less" = marginal (sheltered \
microclimates, rare or greenhouse-only).
- Every note and summary: under 30 words, and written in BOTH English and Russian - the same \
fact in each, not a translation artefact.

Answer with the JSON object {"flowering": {"monthly_intensity", "indoor_note"}, \
"flowering_conditions", "native_range": {"zones": [{"country_code", "country", "status"}], \
"summary"}} and nothing else. "monthly_intensity" is 12 integers (0, 1 or 2); \
"country_code" is an ISO 3166-1 alpha-3 code; every note, summary and country name is an \
{"en", "ru"} pair."""

_LOCALIZED_TEXT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["en", "ru"],
    "properties": {
        "en": {"type": "string", "description": "English, under 30 words."},
        "ru": {"type": "string", "description": "Russian, under 30 words."},
    },
}

_SPECIES_PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["flowering", "flowering_conditions", "native_range"],
    "properties": {
        "flowering": {
            "type": "object",
            "additionalProperties": False,
            "required": ["monthly_intensity", "indoor_note"],
            "properties": {
                # Exact length stays a prompt rule + a sanitiser check:
                # minItems/maxItems are not part of the strict-mode keyword set
                # every provider accepts, and _sanitize_monthly_intensity
                # already rejects any array that isn't 12 long.
                "monthly_intensity": {
                    "type": "array",
                    "description": (
                        "Exactly 12 values, January to December: "
                        "0 no bloom, 1 some bloom, 2 peak bloom."
                    ),
                    "items": {"type": "integer", "enum": [0, 1, 2]},
                },
                "indoor_note": {
                    **_LOCALIZED_TEXT_SCHEMA,
                    "description": "How flowering behaves for this species indoors.",
                },
            },
        },
        "flowering_conditions": {
            **_LOCALIZED_TEXT_SCHEMA,
            "description": "What the owner must provide to get it to bloom.",
        },
        "native_range": {
            "type": "object",
            "additionalProperties": False,
            "required": ["zones", "summary"],
            "properties": {
                "zones": {
                    "type": "array",
                    "description": "3 to 6 unique countries.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["country_code", "country", "status"],
                        "properties": {
                            "country_code": {
                                "type": "string",
                                "description": (
                                    "Uppercase ISO 3166-1 alpha-3 country code, "
                                    "e.g. PER. Never a region."
                                ),
                            },
                            "country": {
                                **_LOCALIZED_TEXT_SCHEMA,
                                "description": "Country name in English and Russian.",
                            },
                            "status": {
                                "type": "string",
                                "enum": sorted(_NATIVE_RANGE_STATUSES),
                                "description": (
                                    "grows_more = thrives there; "
                                    "grows_less = marginal."
                                ),
                            },
                        },
                    },
                },
                "summary": {
                    **_LOCALIZED_TEXT_SCHEMA,
                    "description": "One sentence on where the species comes from.",
                },
            },
        },
    },
}


def _sanitize_monthly_intensity(raw: Any) -> list[int] | None:
    """Validate the 12-month bloom-intensity array. ``None`` on any shape
    mismatch so the caller can reject the whole profile rather than render
    a bogus calendar."""
    if not isinstance(raw, list) or len(raw) != 12:
        return None
    out: list[int] = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int):
            return None
        if v not in (0, 1, 2):
            return None
        out.append(v)
    return out


def _sanitize_native_zones(raw: Any) -> list[dict]:
    """Drop malformed zone entries and cap the list — same tolerant style
    as ``_parse_species_diseases``."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for it in raw[:8]:
        if not isinstance(it, dict):
            continue
        raw_country = it.get("country")
        if not isinstance(raw_country, str):
            # v2 shape ({"en": .., "ru": ..}) — not a v1 zone; never stringify.
            continue
        country = raw_country.strip()
        if not country or len(country) > 80:
            continue
        status = it.get("status")
        if status not in _NATIVE_RANGE_STATUSES:
            continue
        out.append({"country": country, "status": status})
    return out


def _clip_note(raw: Any, max_len: int = 240) -> str:
    # Strings only. A v2 payload carries {"en": ..., "ru": ...} dicts here; a
    # permissive str() turned those into Python dict reprs ("{'en': '...'}")
    # which the v1 fallback then served to users and cached for 30 days.
    # Rejecting non-strings makes such a payload fail BOTH parsers, so the
    # request degrades to available:false as this module's discipline requires.
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    return text[:max_len].rstrip() if len(text) > max_len else text


def _localized_pair(raw: Any, *, max_len: int = 240) -> dict[str, str]:
    """Return a safe EN/RU pair from the v2 model shape."""
    if isinstance(raw, dict):
        en = _clip_note(raw.get("en"), max_len)
        ru = _clip_note(raw.get("ru"), max_len)
    else:
        en = _clip_note(raw, max_len)
        ru = ""
    if not en and ru:
        en = ru
    if not ru and en:
        ru = en
    return {"en": en, "ru": ru}


def _sanitize_canonical_zones(raw: Any) -> list[dict]:
    """Validate and deduplicate v2 country zones by ISO alpha-3 code."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("country_code") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code) or code in seen:
            continue
        status = item.get("status")
        if status not in _NATIVE_RANGE_STATUSES:
            continue
        names = item.get("country")
        if not isinstance(names, dict):
            names = {"en": item.get("country_en"), "ru": item.get("country_ru")}
        pair = _localized_pair(names, max_len=80)
        if not pair["en"] and not pair["ru"]:
            continue
        seen.add(code)
        out.append({"country_code": code, "country": pair, "status": status})
        if len(out) == 6:
            break
    return out


def _parse_species_profile_v2(text: str) -> dict | None:
    """Parse the locale-independent canonical v2 profile returned by the AI."""
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    flowering_raw = data.get("flowering")
    flowering_raw = flowering_raw if isinstance(flowering_raw, dict) else {}
    monthly = _sanitize_monthly_intensity(flowering_raw.get("monthly_intensity"))
    if monthly is None:
        return None
    indoor = _localized_pair(flowering_raw.get("indoor_note"))
    conditions = _localized_pair(data.get("flowering_conditions"))
    native_raw = data.get("native_range")
    native_raw = native_raw if isinstance(native_raw, dict) else {}
    zones = _sanitize_canonical_zones(native_raw.get("zones"))
    summary = _localized_pair(native_raw.get("summary"))
    if not conditions["en"] or not conditions["ru"] or not zones:
        return None
    if not summary["en"] or not summary["ru"]:
        return None
    return {
        "flowering": {"monthly_intensity": monthly, "indoor_note": indoor},
        "flowering_conditions": conditions,
        "native_range": {"zones": zones, "summary": summary},
    }


def _legacy_profile_to_canonical(profile: dict, lang: str) -> dict:
    """Adapt a v1 model reply for a one-deploy compatibility fallback."""
    language = "ru" if lang == "ru" else "en"
    other = "ru" if language == "en" else "en"
    flowering = profile.get("flowering") or {}
    conditions = _clip_note(profile.get("flowering_conditions"))
    native = profile.get("native_range") or {}
    summary = _clip_note(native.get("summary"))
    zones = []
    seen_countries: set[str] = set()
    for zone in native.get("zones", []):
        if not isinstance(zone, dict):
            continue
        country = _clip_note(zone.get("country"), 80)
        status = zone.get("status")
        country_key = re.sub(r"\s+", " ", country.casefold()).strip()
        if country and country_key not in seen_countries and status in _NATIVE_RANGE_STATUSES:
            seen_countries.add(country_key)
            zones.append({
                "country_code": "",
                "country": {"en": country, "ru": country},
                "status": status,
            })
    indoor = _clip_note(flowering.get("indoor_note"))
    return {
        "flowering": {
            "monthly_intensity": flowering.get("monthly_intensity", []),
            "indoor_note": {language: indoor, other: indoor},
        },
        "flowering_conditions": {language: conditions, other: conditions},
        "native_range": {
            "zones": zones[:6],
            "summary": {language: summary, other: summary},
        },
    }


def _profile_response(canonical: dict, species: str, lang: str) -> dict:
    """Render a canonical profile into the stable locale-facing wire shape."""
    language = "ru" if lang == "ru" else "en"
    flowering = canonical["flowering"]
    conditions = canonical["flowering_conditions"]
    native = canonical["native_range"]
    zones = [
        {
            "country": zone["country"].get(language) or zone["country"].get("en", ""),
            "country_code": zone.get("country_code", ""),
            "status": zone["status"],
        }
        for zone in native["zones"]
    ]
    return {
        "available": True,
        "species": species,
        "flowering": {
            "monthly_intensity": flowering["monthly_intensity"],
            "indoor_note": flowering["indoor_note"].get(language) or flowering["indoor_note"].get("en", ""),
        },
        "flowering_conditions": conditions.get(language) or conditions.get("en", ""),
        "native_range": {
            "zones": zones,
            "summary": native["summary"].get(language) or native["summary"].get("en", ""),
        },
        "localized_text": {
            "en": {
                "indoor_note": flowering["indoor_note"].get("en", ""),
                "flowering_conditions": conditions.get("en", ""),
                "native_range_summary": native["summary"].get("en", ""),
            },
            "ru": {
                "indoor_note": flowering["indoor_note"].get("ru", ""),
                "flowering_conditions": conditions.get("ru", ""),
                "native_range_summary": native["summary"].get("ru", ""),
            },
        },
        "source": "openrouter",
    }


def _parse_species_profile(text: str) -> dict | None:
    """Extract + sanitise the species profile from the model's JSON reply.

    Tolerant of code fences / leading prose (grabs the first ``{...}``
    block), but unlike ``_parse_species_diseases`` this returns a single
    object, not a list, so any structurally-required part missing (the
    12-month array, at least one native zone, the summary sentences)
    invalidates the whole profile — a half-populated card is worse than
    none. Returns ``None`` on any problem.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    flowering_raw = data.get("flowering")
    flowering_raw = flowering_raw if isinstance(flowering_raw, dict) else {}
    monthly_intensity = _sanitize_monthly_intensity(flowering_raw.get("monthly_intensity"))
    if monthly_intensity is None:
        return None
    indoor_note = _clip_note(flowering_raw.get("indoor_note"))

    flowering_conditions = _clip_note(data.get("flowering_conditions"))
    if not flowering_conditions:
        return None

    native_raw = data.get("native_range")
    native_raw = native_raw if isinstance(native_raw, dict) else {}
    zones = _sanitize_native_zones(native_raw.get("zones"))
    summary = _clip_note(native_raw.get("summary"))
    if not zones or not summary:
        return None

    return {
        "flowering": {"monthly_intensity": monthly_intensity, "indoor_note": indoor_note},
        "flowering_conditions": flowering_conditions,
        "native_range": {"zones": zones, "summary": summary},
    }


@ai_services._plain_punctuation_result
def species_profile(
    scientific_name: str | None,
    *,
    lang: str = "en",
) -> dict:
    """Flowering pattern + native range for a species (best-effort, AI).

    Returns ``{"available": True, "species", "flowering", "flowering_conditions",
    "native_range"}`` on success. Returns ``{"available": False}`` when no
    OpenRouter key is set, the species name is blank, or on any failure — the
    client then hides/omits the section, so the species screen degrades
    gracefully instead of showing bogus data. Never raises.
    """
    name = (scientific_name or "").strip()
    if not name:
        return {"available": False, "source": "no_species"}
    lang = "ru" if ai_services._is_russian(lang) else "en"
    if not ai_services._chat_api_key_present():
        return {"available": False, "source": "no_api_key"}

    # Generation v5 is deliberately keyed only by scientific name — and the
    # scientific name is now the only caller input in the prompt. v5 retires
    # every v4 profile, which was built from a prompt that also carried the
    # caller's unkeyed common name (see ``_prompt_subject``), so any of them
    # could have been shaped by one client's input for the full THIRTY-day
    # TTL. Superseded entries are never read again; their JSON files are
    # harmless and can be deleted.
    # The cached value is the canonical bilingual profile; locale-specific wire
    # fields are rendered from it on every request.
    cache_key = f"v5:{name.lower()}"
    # A profile built by the legacy adapter is NOT bilingual — that adapter
    # copies the single language the model replied in into both slots. Storing
    # it under the shared key would serve e.g. Russian text to every English
    # client for the whole 30-day TTL, so it gets a locale-scoped key instead.
    legacy_key = f"v5:{lang}:{name.lower()}"
    for key in (cache_key, legacy_key):
        cached = _SPECIES_PROFILE_CACHE.get(key)
        if cached is not None:
            return {**_profile_response(cached, name, lang), "cached": True}
    # Fall through to the persistent layer before paying for the LLM again —
    # this is what survives a server restart / a fresh app install.
    for key in (cache_key, legacy_key):
        disk_hit = _SPECIES_PROFILE_DISK.get(key)
        if disk_hit is not None:
            _SPECIES_PROFILE_CACHE.put(key, disk_hit)
            return {**_profile_response(disk_hit, name, lang), "cached": True}

    subject = ai_services._prompt_subject(name)
    user = f"Species: {subject}"
    try:
        # Same caching shape as species_common_diseases: the shared preamble
        # plus this task block is a byte-identical prefix on every profile
        # request, so it is the one thing here that a warm cache can serve.
        text, _tin, _tout, _cache = ai_services._call_openrouter(
            ai_services._system_prompt(_SPECIES_PROFILE_TASK),
            [{"role": "user", "content": user}],
            max_tokens=SPECIES_PROFILE_MAX_TOKENS,
            require_complete=True,
            reasoning_effort=SPECIES_PROFILE_REASONING_EFFORT,
            json_schema=_SPECIES_PROFILE_SCHEMA,
            schema_name="species_profile",
        )
    except Exception:  # noqa: BLE001 — never propagate provider details
        logger.warning("Species profile lookup failed")
        return {"available": False, "source": "api_error"}

    canonical = _parse_species_profile_v2(text)
    from_legacy = False
    if canonical is None:
        # A short-lived compatibility fallback for a provider/model still
        # returning the pre-v2 shape. New responses always use v2 above.
        legacy = _parse_species_profile(text)
        if legacy:
            canonical = _legacy_profile_to_canonical(legacy, lang)
            from_legacy = True
    if canonical is None:
        return {"available": False, "source": "empty"}
    store_key = legacy_key if from_legacy else cache_key
    _SPECIES_PROFILE_CACHE.put(store_key, canonical)
    _SPECIES_PROFILE_DISK.put(store_key, canonical)
    return _profile_response(canonical, name, lang)
