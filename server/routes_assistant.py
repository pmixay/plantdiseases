"""LLM-backed knowledge: Plant Doctor chat, care plans, species cards.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# chat_reply outcomes decided before any model call.
# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import diseases_data
except ImportError:
    import diseases_data


_CHAT_NO_COST_REASONS = frozenset({
    "empty_message",
    "message_too_long",
    "no_api_key",
    "session_turn_cap",
    "session_token_cap",
})


_QUOTA_UNAVAILABLE_TEXT = {
    "en": "You've reached today's limit for this feature. It resets at midnight UTC.",
    "ru": "Дневной лимит для этой функции исчерпан. Он обновится в полночь по UTC.",
}


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=2000)
    locale: str = Field(default="en", max_length=8)
    diagnosis: dict | None = None


class CarePlanRequest(BaseModel):
    disease: str = Field(..., min_length=1, max_length=64)
    species: str | None = Field(default=None, max_length=128)
    light: str = Field(default="bright_indirect", max_length=32)
    locale: str = Field(default="en", max_length=8)
    # Optional season override; ``None`` auto-detects from today's date
    # (northern-hemisphere default). Accepted values: ``"growing"``
    # or ``"dormant"``.
    season: str | None = Field(default=None, max_length=16)
    # What the plan is for. ``scan``: adapted to a diagnosis (the scan result
    # and the plant it was saved to); a fresh AI summary spends the daily
    # care-plan allowance. ``species``: a species' general care (species
    # page), always the healthy-plant plan and never metered. ``checklist``:
    # the structured plan behind a garden treatment checklist, no AI summary,
    # never metered.
    purpose: Literal["scan", "species", "checklist"] = "scan"


def register(router: APIRouter, m) -> None:
    @router.post("/chat")
    async def chat_endpoint(request: Request, payload: ChatRequest):
        """AI Plant Doctor — OpenRouter chat completion (default model
        ``openai/gpt-5.6-luna``).

        Per-session turn + token caps prevent budget overrun within a session;
        ``CHAT_DAILY_QUOTA`` bounds turns per IP across sessions, since the
        client mints ``session_id`` and a new one resets the session caps.
        """
        if not m._chat_quota.try_consume(m._client_key(m._client_ip(request))):
            lang = "ru" if str(payload.locale or "en").lower().startswith("ru") else "en"
            return JSONResponse(content={
                "available": False,
                "reply": "",
                "reason": "daily_quota",
                # The app shows ``fallback`` verbatim, so builds that predate
                # this reason still tell the user what happened.
                "fallback": _QUOTA_UNAVAILABLE_TEXT[lang],
            })
        # Pydantic doesn't bound dict size; reject oversize diagnosis blobs
        # before they hit chat_reply so a malicious client can't push a
        # 10 MB nested dict through the LLM context window.
        diagnosis = payload.diagnosis
        if diagnosis is not None and len(json.dumps(diagnosis, default=str)) > 8 * 1024:
            raise HTTPException(status_code=400, detail="diagnosis context too large")
        metered = m.ai_services.openrouter_configured()
        if metered:
            await m._authorize_metered_feature(request, feature="ai_doctor")
        result = await asyncio.get_running_loop().run_in_executor(
            m._get_net_executor(),
            lambda: m.ai_services.chat_reply(
                session_id=payload.session_id,
                user_message=payload.message,
                locale=payload.locale,
                diagnosis_context=diagnosis,
            ),
        )
        if metered and result.get("reason") in _CHAT_NO_COST_REASONS:
            # Refused before any model call: the message costs nothing, so it
            # does not count against the daily allowance.
            await m._release_metered_feature_if_no_upstream_attempt(
                request,
                feature="ai_doctor",
                upstream_attempted=False,
            )
        if (
            not result.get("available")
            and result.get("reason") in ("session_turn_cap", "session_token_cap")
        ):
            m.logger.warning("AI Doctor budget cap reached")
        return JSONResponse(content=result)


    @router.post("/care-plan")
    async def care_plan_endpoint(request: Request, payload: CarePlanRequest):
        """Build a watering / fertilising / humidity schedule for a plant.

        Always returns a usable rules-based plan; when an OpenRouter key
        is configured the response also includes a Markdown ``summary_md``
        polished by the LLM. Disease must be one of the known classifier
        classes (``unknown`` is normalised to a generic monitoring plan).
        Past ``CARE_PLAN_DAILY_QUOTA`` for the caller's IP the plan is served
        without the LLM polish.

        Only a plan adapted to a scan (``purpose="scan"``, the default) spends
        the caller's daily care-plan allowance, and only for a fresh LLM
        polish: a cached polished summary and the rules-based plan cost
        nothing. A species page's general plan (``purpose="species"``) is
        always the healthy-plant plan and never metered; its polish is still
        bounded by the per-IP allowance and shared through the cache. A garden
        checklist (``purpose="checklist"``) needs only the structured plan, so
        it is never polished or metered.
        """
        if m.pipeline is None:
            raise HTTPException(status_code=503, detail="Server starting up, try again shortly")
        purpose = payload.purpose
        disease = payload.disease.strip()
        if purpose == "species":
            # A species' general care, never a diagnosis: keeps this unmetered
            # path from serving disease-specific plans.
            disease = "healthy"
        if disease not in diseases_data.DISEASES_DATABASE and disease != "unknown":
            # Don't reject — just normalise unknown to ``unknown``. Keeps the
            # endpoint forgiving while still producing a useful plan.
            disease = "unknown"
        loop = asyncio.get_running_loop()
        polish = purpose != "checklist"
        metered = purpose == "scan"

        def build(*, polish: bool, cached_polish_only: bool = False) -> dict:
            return m.ai_services.generate_care_plan(
                disease=disease,
                species=payload.species,
                light=payload.light,
                locale=payload.locale,
                season=payload.season,
                polish=polish,
                cached_polish_only=cached_polish_only,
            )

        plan = await loop.run_in_executor(
            m._get_net_executor(),
            lambda: build(polish=polish, cached_polish_only=True),
        )
        if (
            polish
            and not plan.get("cached")
            and m.ai_services.openrouter_configured()
            and m._care_plan_quota.try_consume(m._client_key(m._client_ip(request)))
        ):
            if metered:
                await m._authorize_metered_feature(request, feature="care_plan")
            plan = await loop.run_in_executor(
                m._get_net_executor(),
                lambda: build(polish=True),
            )
            if metered:
                # Another request may have cached the same summary meanwhile.
                await m._release_metered_feature_if_no_upstream_attempt(
                    request,
                    feature="care_plan",
                    upstream_attempted=not plan.get("cached"),
                )
        toxicity = diseases_data.lookup_pet_toxicity(payload.species) if payload.species else {
            "toxic_to_pets": False, "species_list": [], "source": "unknown",
        }
        plan = {
            **plan,
            "toxic_to_pets": toxicity["toxic_to_pets"],
            "toxic_species_list": toxicity["species_list"],
            "toxicity_source": toxicity["source"],
        }
        return JSONResponse(content=plan)


    @router.get("/species-info")
    async def species_info_endpoint(name: str, locale: str = "en"):
        """Localized Wikipedia summary + lead image for a scientific name."""
        n = (name or "").strip()
        if not n or len(n) > 128:
            raise HTTPException(status_code=422, detail="Invalid species name")
        lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            m._get_net_executor(), lambda: m.species_info.fetch_species_info(n, lang),
        )
        if info is None:
            # Don't let an intermediary cache a transient Wikipedia miss.
            return JSONResponse(content={"available": False}, headers={"Cache-Control": "no-store"})
        # Pin the PHOTOS to the English article for every locale so the same
        # species shows the same images regardless of UI language — the localized
        # (e.g. RU) Wikipedia article often carries a different or smaller media
        # set, which made the species card's gallery differ by locale. Text (title,
        # extract) stays localized; only the images come from EN. Fall back to the
        # localized images when the English article has none, so a card never loses
        # its photo. (fetch_species_info is cached, so the extra EN call is cheap.)
        image_url = info.get("image_url")
        images = info.get("images") or []
        credit, licence = info.get("image_credit"), info.get("image_license")
        if lang != "en":
            en_info = await loop.run_in_executor(
                m._get_net_executor(), lambda: m.species_info.fetch_species_info(n, "en"),
            )
            if en_info and (en_info.get("images") or en_info.get("image_url")):
                image_url = en_info.get("image_url")
                images = en_info.get("images") or ([image_url] if image_url else [])
                credit, licence = en_info.get("image_credit"), en_info.get("image_license")
        # Route the Wikipedia photos through the server's cache so the app loads
        # them from our domain (reliable where Wikimedia is blocked). Unproxyable
        # entries drop to None/out so the card just omits them.
        # Fetch the gallery into the photo cache now, politely and one at a
        # time, so the card's burst of image requests finds them cached rather
        # than tripping Wikimedia's throttle (see plant_photos.warm).
        m.plant_photos.warm([image_url, *images])
        proxied = {
            **info,
            "image_url": m.plant_photos.proxy_path(image_url),
            "images": [p for p in (m.plant_photos.proxy_path(i) for i in images) if p],
            # Author + licence of the photo (set for Commons / iNaturalist
            # fallbacks; null for a Wikipedia lead image).
            "image_credit": credit,
            "image_license": licence,
        }
        # Species facts are stable; carry a long Cache-Control so a CDN / HTTP cache
        # reuses it too (symmetric with /species-diseases). The app has its own
        # 7-day ResponseCache, so this mainly helps any intermediary layer.
        # A degraded answer (genus-level, or no photo yet) is revalidated soon
        # so the species-level / photo-bearing answer replaces it once found.
        complete = info.get("level") != "genus" and bool(proxied["image_url"])
        return JSONResponse(
            content={"available": True, **proxied},
            headers={
                "Cache-Control": "public, max-age=604800" if complete else "public, max-age=900",
            },
        )


    @router.get("/species-diseases")
    async def species_diseases_endpoint(
        request: Request, name: str, common: str = "", locale: str = "en",
    ):
        """Common conditions a species is affected by indoors (AI, best-effort).

        Graceful ``{"available": False, "diseases": []}`` when no OpenRouter key
        is configured or on any failure — the client then renders its bundled
        disease guide, so the species screen's "common problems" section is never
        empty. A real (``available``) result is cached server-side (in-memory +
        on disk, surviving restarts) per (species, language) and carries a long
        ``Cache-Control`` so the client/CDN reuses it too; a transient failure is
        sent uncached so the next open retries. Past ``SPECIES_AI_DAILY_QUOTA``
        for the caller's IP the answer is ``available: false`` with
        ``reason: "daily_quota"``.
        """
        n = (name or "").strip()
        if not n or len(n) > 128:
            raise HTTPException(status_code=422, detail="Invalid species name")
        if not m._species_ai_quota.try_consume(m._client_key(m._client_ip(request))):
            return JSONResponse(
                content={"available": False, "diseases": [], "reason": "daily_quota"},
                headers={"Cache-Control": "no-store"},
            )
        lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
        # ``common`` is accepted for client compatibility and ignored: it is not
        # part of the shared cache key, so it must not shape the cached answer.
        _ = common
        data = await asyncio.get_running_loop().run_in_executor(
            m._get_net_executor(), lambda: m.ai_services.species_common_diseases(n, lang=lang),
        )
        headers = (
            {"Cache-Control": "public, max-age=604800"}
            if isinstance(data, dict) and data.get("available")
            else {"Cache-Control": "no-store"}
        )
        return JSONResponse(content=data, headers=headers)


    @router.get("/species-profile")
    async def species_profile_endpoint(
        request: Request,
        name: str,
        common: str = "",
        locale: str = "en",
        profile_version: int = 3,
    ):
        """Flowering pattern + native range for a species (AI, best-effort).

        Graceful ``{"available": False}`` when no OpenRouter key is configured or
        on any failure — the client then hides the section, so the species screen
        degrades gracefully instead of showing bogus data. A real (``available``)
        result is cached server-side (in-memory + on disk, surviving restarts) per
        species and carries both EN/RU text copies, so a locale switch cannot
        change the map. ``profile_version`` is a cache-busting query parameter for
        clients/CDNs; the current endpoint always serves the canonical bilingual shape.
        Shares ``SPECIES_AI_DAILY_QUOTA`` with ``/api/species-diseases``.
        """
        _ = profile_version
        n = (name or "").strip()
        if not n or len(n) > 128:
            raise HTTPException(status_code=422, detail="Invalid species name")
        if not m._species_ai_quota.try_consume(m._client_key(m._client_ip(request))):
            return JSONResponse(
                content={"available": False, "reason": "daily_quota"},
                headers={"Cache-Control": "no-store"},
            )
        lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
        # ``common`` is accepted for client compatibility and ignored, as above.
        _ = common
        data = await asyncio.get_running_loop().run_in_executor(
            m._get_net_executor(), lambda: m.ai_services.species_profile(n, lang=lang),
        )
        headers = (
            {"Cache-Control": "public, max-age=604800"}
            if isinstance(data, dict) and data.get("available")
            else {"Cache-Control": "no-store"}
        )
        return JSONResponse(content=data, headers=headers)
