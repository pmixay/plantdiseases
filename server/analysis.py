"""The /api/analyze and /api/identify engine behind ``routes_diagnosis``.

Everything here that reads process-wide state (the pipelines, quotas, metering,
executors, limits) takes the ``main`` module as its first argument, ``m``, and
reads that state as ``m.name`` at call time, so a test that patches a name on
``main`` is seen exactly as it was when these lived there.
"""

import asyncio
import contextlib
import copy
import functools
import hashlib
import io
import logging
import time
import uuid

from fastapi import BackgroundTasks, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image

# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import ai_services
except ImportError:
    import ai_services
try:
    from . import analysis_payload
except ImportError:
    import analysis_payload
try:
    from . import community, community_popular
except ImportError:
    import community
    import community_popular
try:
    from . import diseases_data
except ImportError:
    import diseases_data
try:
    from . import pipeline as pipeline_module
except ImportError:
    import pipeline as pipeline_module
try:
    from . import synthetic_pipeline
except ImportError:
    import synthetic_pipeline
try:
    from . import training_store
except ImportError:
    import training_store


logger = logging.getLogger("plantdiseases")


def analyze_cache_get(m, cache_key: str) -> dict | None:
    """Return a previously-stored analyse response keyed by
    ``<image_sha>:<plantnet_lang>``, or ``None`` if there's nothing
    fresh under that key. Updates LRU recency on hit so frequent
    re-uploads stay warm."""
    now = time.time()
    with m._analyze_cache_lock:
        item = m._analyze_cache.get(cache_key)
        if item is None:
            return None
        cached_at, payload = item
        if now - cached_at > m.ANALYZE_CACHE_TTL_SECONDS:
            m._analyze_cache.pop(cache_key, None)
            return None
        m._analyze_cache.move_to_end(cache_key)
        return {"cached_at": cached_at, "payload": payload}


def analyze_cache_put(m, cache_key: str, payload: dict) -> None:
    now = time.time()
    with m._analyze_cache_lock:
        m._analyze_cache[cache_key] = (now, payload)
        m._analyze_cache.move_to_end(cache_key)
        while len(m._analyze_cache) > m.ANALYZE_CACHE_MAX_ENTRIES:
            m._analyze_cache.popitem(last=False)


def normalise_family(m, family: str | None) -> str:
    """Map a requested family string to a known tier, defaulting to gamma.

    Unknown values fall back to the default rather than erroring — a
    forward-compatible client sending a future family name still gets a
    usable diagnosis instead of a 400.
    """
    fam = (family or "").strip().lower()
    return fam if fam in m.KNOWN_FAMILIES else m.DEFAULT_FAMILY


def pipeline_for_family(m, family: str) -> pipeline_module.PlantDiseasePipeline | synthetic_pipeline.SyntheticPipeline | None:
    """Resolve the pipeline instance that should serve this family."""
    if family == "frontier":
        return m.pipeline_frontier
    # gamma + aqua (server stand-in) both run on the base pipeline.
    return m.pipeline


def persist_community_analysis_token(
    m,
    token: str,
    *,
    disease: str,
    confidence: float | None,
    species: str | None,
    species_score: float | None,
    image_sha256: str,
) -> bool:
    """Persist a publication proof; report failure without leaking a phantom token."""
    try:
        community.record_analysis_token(
            token,
            disease=disease,
            confidence=confidence,
            species=species,
            species_score=species_score,
            image_sha256=image_sha256,
            ttl_days=m.COMMUNITY_ANALYSIS_TOKEN_TTL_DAYS,
        )
    except Exception:  # noqa: BLE001 - fixed message; never log the token
        logger.warning("Failed to persist community analysis token")
        return False
    return True


async def issue_interaction_tokens(
    m,
    *,
    capture_pending: bool,
    disease: str,
    confidence: float | None,
    species: str | None,
    species_score: float | None,
    image_sha256: str,
) -> tuple[str | None, str | None]:
    """Mint unrelated community and feedback capabilities for one response."""
    loop = asyncio.get_running_loop()
    community_token = uuid.uuid4().hex
    community_token_persisted = await loop.run_in_executor(
        None,
        lambda: persist_community_analysis_token(
            m,
            community_token,
            disease=disease,
            confidence=confidence,
            species=species,
            species_score=species_score,
            image_sha256=image_sha256,
        ),
    )
    try:
        feedback_receipt = await loop.run_in_executor(
            None,
            lambda: training_store.issue_feedback_receipt(
                capture_pending=capture_pending
            ),
        )
    except Exception:  # noqa: BLE001 - inference remains available, fixed log
        logger.warning("Feedback receipt issuance failed")
        feedback_receipt = None
    return community_token if community_token_persisted else None, feedback_receipt


def metadata_free_external_jpeg(m, image: Image.Image) -> bytes:
    """Return bounded RGB pixels with no source metadata for third parties.

    PlantNet only needs the visible plant pixels.  Rebuilding a fresh image
    before encoding prevents EXIF/GPS, ICC, XMP, IPTC, thumbnails, comments,
    and source filenames from crossing the service boundary even when a
    non-Android client calls the public API directly.
    """
    bounded_side = max(64, min(m.PLANTNET_IMAGE_MAX_DIMENSION, m.MAX_IMAGE_DIMENSION))
    resized = image.convert("RGB").copy()
    if max(resized.size) > bounded_side:
        resized.thumbnail((bounded_side, bounded_side), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", resized.size)
    clean.paste(resized)
    encoded = io.BytesIO()
    clean.save(
        encoded,
        format="JPEG",
        quality=88,
        optimize=True,
        progressive=False,
        subsampling=2,
    )
    return encoded.getvalue()


def sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest. Pulled out so the analyze handler can run it in
    a thread — hashing up to MAX_UPLOAD_BYTES on the event loop would stall
    every other request for the duration."""
    return hashlib.sha256(data).hexdigest()


def record_popular_contribution(species: str | None, disease: str) -> None:
    """Count one accepted diagnosis in the anonymous worldwide popularity
    tally (species + class only; see community_popular.record_anonymous_scan).
    Fail-soft: never affects the response."""
    try:
        community_popular.record_anonymous_scan(species, disease)
    except Exception:  # noqa: BLE001 - background bookkeeping only
        logger.warning("Popular tally update failed")


def record_training_candidate(
    m,
    image: Image.Image,
    feedback_receipt: str,
    *,
    family: str,
    model_version: str,
    result: dict,
    consent_version: str,
) -> None:
    """Store one detector-backed leaf crop after the response, fail-soft."""
    stored = False
    try:
        class_name = str(result.get("class_name") or "")
        if not m.TRAINING_CAPTURE_ENABLED or pipeline_module._derive_abstention(result)[0]:
            return
        detection = result.get("detection") or {}
        safe_crop = training_store.extract_safe_leaf_crop(image, detection)
        if safe_crop is None:
            return
        crop, area_buckets = safe_crop
        sanitized = training_store.sanitize_training_image(crop)
        prediction_set = list(
            (result.get("prediction_set") or {}).get("classes") or []
        )
        confidence = float(result.get("confidence") or 0.0)
        review_status = training_store.route_model_review(
            class_name,
            confidence,
            prediction_set,
            trust_conformal=m.TRAINING_TRUST_CONFORMAL,
            confidence_threshold=m.TRAINING_CONF_HIGH,
        )
        if (
            review_status == "auto_yes"
            and class_name not in m.DISEASE_CLASSES
            and not training_store.deterministically_sample(
                sanitized.sha256, m.TRAINING_HEALTHY_KEEP_RATE
            )
        ):
            return
        if (
            training_store.count_samples_for_day(class_name)
            >= m.TRAINING_DAILY_CAP_PER_CLASS
        ):
            return
        training_store.store_sample(
            sanitized,
            source="consented_scan",
            model_family=family,
            model_version=model_version,
            predicted_class=class_name,
            predicted_confidence=confidence,
            top_k=list(result.get("top_k") or []),
            conformal_set=prediction_set,
            warnings=list(result.get("warnings") or []),
            blur_bucket=training_store.blur_score_bucket(image),
            detector_region_count=len(detection.get("regions") or []),
            bbox_area_buckets=area_buckets,
            severity=str(result.get("severity") or "unknown"),
            review_status=review_status,
            consent_version=consent_version,
            receipt=feedback_receipt,
        )
        stored = True
    except Exception:  # noqa: BLE001 - background capture never affects inference
        logger.warning("Training capture failed")
    finally:
        if not stored:
            try:
                training_store.complete_receipt_without_sample(feedback_receipt)
            except Exception:  # noqa: BLE001 - fixed message, no receipt logging
                logger.warning("Training receipt finalization failed")


async def species_identify_best_effort(
    m,
    image_bytes: bytes,
    *,
    timeout: float,
    lang: str = "en",
    cached: dict | None = None,
) -> dict | None:
    """Run PlantNet identify on the default thread pool with a tight
    timeout (NOT the single-worker inference executor — there it would
    serialise behind / ahead of the pipeline forward instead of running
    in parallel with it). Returns the compact species block ({class, score,
    scientific, common, source}) for the response, or ``None`` if the
    call failed, timed out, or PlantNet isn't configured.

    ``lang`` is forwarded so the returned ``common_name`` is
    localised — without it the carousel shows "Rubber tree" even on
    a Russian-locale device.

    Never raises — every failure mode is logged at INFO and folded
    into ``None`` so the analyze response stays whole.

    Takes the decoded image, not the upload bytes: PlantNet receives a
    metadata-free re-encode (``_plantnet_upload_bytes``), so EXIF/GPS from
    a client that did not strip it never reaches the third party.
    """
    loop = asyncio.get_running_loop()
    try:
        raw = cached
        if raw is None:
            raw = await asyncio.wait_for(
                loop.run_in_executor(
                    m._get_net_executor(),
                    # identify_species keyword args (organ, lang) are
                    # keyword-only — run_in_executor only takes positional
                    # args, so we wrap in a lambda. Passing positionally
                    # raises TypeError at call time and surfaces as a 500.
                    lambda: ai_services.identify_species(
                        image_bytes, organ="auto", lang=lang,
                    ),
                ),
                timeout=timeout,
            )
    except (TimeoutError, asyncio.CancelledError):
        logger.info("Species identify skipped (timeout / cancelled)")
        return None
    except Exception:  # noqa: BLE001
        logger.info("Species identify failed")
        return None
    upstream_attempted = (
        raw.get("_upstream_attempted")
        if isinstance(raw, dict) and type(raw.get("_upstream_attempted")) is bool
        else None
    )
    if not raw or not raw.get("available"):
        unavailable = {
            "available": False,
            "source": raw.get("source", "unavailable") if raw else "unavailable",
            "best": None,
            "candidates": [],
        }
        if upstream_attempted is not None:
            unavailable["_upstream_attempted"] = upstream_attempted
        return unavailable
    candidates = raw.get("results") or []
    if not candidates:
        no_candidates = {
            "available": True,
            "source": raw.get("source", "plantnet"),
            "best": None,
            "candidates": [],
        }
        if upstream_attempted is not None:
            no_candidates["_upstream_attempted"] = upstream_attempted
        return no_candidates
    def _shape(entry: dict) -> dict:
        # PlantNet returns a `common_names` list; we surface only the
        # first one for compactness. The full list stays available via
        # the dedicated /api/identify endpoint when needed.
        common_names = entry.get("common_names") or []
        return {
            "scientific_name": entry.get("scientific_name"),
            "common_name": (common_names[0] if common_names else None),
            "score": round(float(entry.get("score", 0.0)), 4),
        }
    shaped = {
        "available": True,
        "source": raw.get("source", "plantnet"),
        "best": _shape(candidates[0]),
        "candidates": [_shape(c) for c in candidates[:3]],
    }
    if upstream_attempted is not None:
        shaped["_upstream_attempted"] = upstream_attempted
    return shaped


async def plantnet_quota_exhausted() -> dict:
    """Species block for a scan whose IP has used up its PlantNet allowance."""
    return {"available": False, "source": "quota", "best": None, "candidates": []}


async def analyze_image_admitted(
    m,
    request: Request,
    background_tasks: BackgroundTasks,
    image: UploadFile,
    slot,
    *,
    client_ip: str,
    species: str | None,
    locale: str,
    family: str,
    training_consent: bool,
    training_consent_version: str | None,
) -> JSONResponse:
    """The body of ``/api/analyze``, run while holding an inference slot."""
    if m.pipeline is None:
        m._record_error()
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")

    consent_version = (training_consent_version or "").strip().lower()
    expected_consent_version = m.TRAINING_CONSENT_VERSION.lower()
    if training_consent and consent_version != expected_consent_version:
        raise HTTPException(status_code=422, detail="training_consent_version_invalid")

    family = normalise_family(m, family)
    requested_family = family
    # Frontier gate: short-circuit BEFORE doing any decode/inference work
    # when the family isn't available on this server. Mirrors the optional-
    # feature pattern (PlantNet / chat): HTTP 200 with ``available:false``
    # so the client renders "unavailable" instead of treating it as a hard
    # error. The app additionally checks /api/capabilities up front to grey
    # the option out, so this is the defensive backstop.
    if family == "frontier" and not m._family_available("frontier"):
        return JSONResponse(content={
            "available": False,
            "family": "frontier",
            "reason": "frontier_disabled",
            "message": (
                "Frontier inference is not enabled on this server. "
                "Pick Gamma for an online diagnosis, or Aqua for offline."
            ),
        })
    serving_pipeline = pipeline_for_family(m, family) or m.pipeline
    classifier_meta = getattr(serving_pipeline.classifier, "meta", {})
    model_version = str(
        (classifier_meta or {}).get("model_version")
        or ("synthetic-test" if m.ACCOUNT_BETA_SYNTHETIC_PIPELINE else "unknown")
    )
    served_family_raw = getattr(serving_pipeline.classifier, "model_family", None)
    served_family = (
        str(served_family_raw).strip().lower()
        if isinstance(served_family_raw, str) and served_family_raw.strip()
        else m.DEFAULT_FAMILY
    )
    family_fallback_reason = (
        None
        if served_family == requested_family
        else (
            "aqua_served_by_gamma"
            if requested_family == "aqua" and served_family == "gamma"
            else "requested_family_fallback"
        )
    )

    start_time = time.time()

    if not image.content_type or image.content_type not in m.ALLOWED_CONTENT_TYPES:
        m._record_error()
        logger.warning("Analyze upload type rejected")
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Allowed: JPEG, PNG, WebP, BMP",
        )

    try:
        contents = await m._read_upload(image)
    except HTTPException:
        m._record_error()
        raise
    except Exception as exc:
        m._record_error()
        logger.warning("Analyze upload read failed")
        raise HTTPException(status_code=400, detail="Could not read uploaded file") from exc

    loop = asyncio.get_running_loop()
    try:
        m._validate_image_bytes(contents)
        # Decode off the event loop. A large JPEG decode (img.load()) can
        # block the loop for tens of ms — long enough to stall every other
        # in-flight request — so it runs on the default threadpool. The
        # cheap magic-byte check stays inline.
        img = await loop.run_in_executor(None, m._open_image, contents)
    except HTTPException:
        m._record_error()
        raise
    except Exception as exc:
        # Guard against any non-HTTPException leaking from PIL — return a
        # consistent 400 instead of letting the request crash with 500.
        m._record_error()
        logger.warning("Analyze image validation failed")
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc

    img_w, img_h = img.size
    if img_w > m.MAX_IMAGE_DIMENSION or img_h > m.MAX_IMAGE_DIMENSION:
        m._record_error()
        logger.warning("Analyze image dimensions rejected")
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too large ({img_w}x{img_h}). "
                   f"Maximum dimension: {m.MAX_IMAGE_DIMENSION}px",
        )
    if min(img_w, img_h) < m.MIN_IMAGE_DIMENSION:
        m._record_error()
        logger.warning("Analyze image dimensions rejected")
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too small ({img_w}x{img_h}). "
                   f"Minimum dimension: {m.MIN_IMAGE_DIMENSION}px - please retake the photo.",
        )

    # Same-photo short-circuit. The hash is over the raw upload bytes,
    # not the decoded pixels, so even a re-encode produces a fresh
    # entry (which is the right call: re-encoded photos can differ
    # under quantisation in ways that flip the verdict). Cached
    # responses are re-emitted byte-identical with two added fields:
    # ``is_repeat=true`` and ``cached_at`` so the client can render
    # a "you saw this one earlier" banner instead of pretending it's
    # a fresh diagnosis.
    #
    # The cache key is (image_hash, plantnet_lang). The ``?species=``
    # override drives toxicity lookup only — see the auto/explicit
    # fall-through ~80 lines below — so on a cache hit we re-resolve
    # toxicity against the override and patch the three affected
    # fields, leaving the verdict / detection / care-plan-relevant
    # fields untouched (they're properties of the image, not of the
    # user's species hint). The species block, however, IS locale-
    # dependent — PlantNet's ``common_name`` comes back in the
    # request's ``lang``. Without ``plantnet_lang`` in the cache key,
    # a Russian user's first upload poisons the cached species block
    # with Russian names for every later English-locale viewer of the
    # same image (and vice versa).
    plantnet_lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    image_sha = await loop.run_in_executor(None, sha256_hex, contents)
    # Family is part of the cache key: gamma and frontier are different
    # models and can land on different verdicts for the same bytes, so a
    # gamma cache hit must never be replayed for a frontier request.
    cache_key = f"{image_sha}:{plantnet_lang}:{family}"
    cache_hit = analyze_cache_get(m, cache_key)
    if cache_hit is not None:
        # Deep-copy so the toxicity-override patch below (and any
        # incidental mutation by downstream middleware) can't poison
        # the cached entry for the next reader. A shallow ``dict(...)``
        # only copies the top level — nested ``species.candidates`` /
        # ``top_k`` / ``detection.regions`` would still be aliased.
        cached_payload = copy.deepcopy(cache_hit["payload"])
        # Capabilities are fresh per response and must never be shared through
        # the diagnosis cache, even when two callers upload identical bytes.
        cached_payload.pop("scan_id", None)
        cached_payload.pop("feedback_receipt", None)
        cached_payload["is_repeat"] = True
        cached_payload["cached_at"] = cache_hit["cached_at"]
        cached_payload["elapsed_ms"] = 0
        cached_payload["family"] = served_family
        cached_payload["requested_family"] = requested_family
        cached_payload["family_fallback_reason"] = family_fallback_reason
        analysis_payload.apply_abstention_presentation(cached_payload)
        species_override = (species or "").strip() or None
        if species_override and not cached_payload["abstained"]:
            overridden = diseases_data.lookup_pet_toxicity(species_override)
            cached_payload["toxic_to_pets"] = overridden["toxic_to_pets"]
            cached_payload["toxic_species_list"] = overridden["species_list"]
            cached_payload["toxicity_source"] = overridden["source"]
        capture_pending = bool(
            m.TRAINING_CAPTURE_ENABLED
            and training_consent
            and not cached_payload["abstained"]
        )
        cached_species = (cached_payload.get("species") or {}).get("best") or {}
        community_token, feedback_receipt = await issue_interaction_tokens(
            m,
            capture_pending=capture_pending,
            disease=(
                "not_a_plant" if cached_payload["abstained"]
                else str(cached_payload.get("class_name") or "")
            ),
            confidence=cached_payload.get("confidence"),
            species=cached_species.get("scientific_name"),
            species_score=cached_species.get("score"),
            image_sha256=image_sha,
        )
        cached_payload["scan_id"] = community_token
        cached_payload["feedback_receipt"] = feedback_receipt
        if capture_pending and feedback_receipt is not None:
            background_tasks.add_task(
                record_training_candidate, m,
                img.copy(),
                feedback_receipt,
                family=family,
                model_version=model_version,
                result=copy.deepcopy(cached_payload),
                consent_version=consent_version,
            )
        return JSONResponse(content=cached_payload)

    # Only uncached work spends a daily allowance: a repeated photo is served
    # from the cache above for free. Malformed or unsupported photos were
    # rejected earlier, so exactly one metered operation is reserved here,
    # before inference.
    await m._authorize_metered_feature(
        request,
        feature=("frontier_analysis" if family == "frontier" else "gamma_analysis"),
    )

    # A first-time diagnosis also offers a best-effort PlantNet species block.
    # That is a real vendor identification call, so it shares the *same*
    # plant-identification allowance as the explicit /api/identify surface.
    # Without this second authorization, 30 PRO Gamma scans plus 3 explicit
    # identifies could silently multiply paid vendor calls even though the
    # launch product contract caps Plant identification at 3/day for PRO.
    # Cached diagnosis responses never reach this branch and therefore spend
    # neither a user identification unit nor a vendor call.
    species_task: asyncio.Task | None = None
    species_payload: dict | None = None
    species_authorized = False
    species_image_bytes = await loop.run_in_executor(
        None,
        metadata_free_external_jpeg, m,
        img.copy(),
    )
    cached_species = await loop.run_in_executor(
        None,
        functools.partial(
            ai_services.plantnet_cache_lookup,
            species_image_bytes,
            organ="auto",
            lang=plantnet_lang,
        ),
    )
    if cached_species is None and not m._plantnet_quota.try_consume(client_ip):
        # This IP has used its daily PlantNet allowance: the scan still gets
        # its diagnosis, without the species block. A cache hit costs no
        # lookup and is never counted.
        species_payload = await plantnet_quota_exhausted()
    elif cached_species is None and ai_services.plantnet_configured():
        species_payload = await m._authorize_optional_species_lookup(request)
        species_authorized = species_payload is None
    if species_payload is None:
        # Fire PlantNet in parallel with the disease pipeline. PlantNet remains
        # best-effort: timeout/provider failure yields an unavailable species
        # block while the diagnosis succeeds. The 10s cap matches one full
        # configured HTTP attempt and stays below the inference timeout.
        species_task = asyncio.create_task(
            species_identify_best_effort(
                m,
                species_image_bytes,
                timeout=10.0,
                lang=plantnet_lang,
                cached=cached_species,
            )
        )
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(m._get_inference_executor(), serving_pipeline.analyze, img),
            timeout=m.INFERENCE_TIMEOUT,
        )
    except TimeoutError as exc:
        if species_task is not None:
            species_task.cancel()
        m._record_error()
        logger.error("Inference timed out")
        raise HTTPException(
            status_code=504,
            detail=f"Analysis timed out after {int(m.INFERENCE_TIMEOUT)} seconds",
        ) from exc
    except Exception as exc:
        if species_task is not None:
            species_task.cancel()
        m._record_error()
        logger.error("Inference failed")
        raise HTTPException(status_code=500, detail="Internal inference error") from exc
    # Inference is done: give the slot back before waiting on PlantNet.
    slot.release()

    class_name = result["class_name"]
    confidence = result["confidence"]
    abstained, abstention_reason = pipeline_module._derive_abstention(result)

    elapsed = time.time() - start_time
    metric_class = (
        f"abstained:{abstention_reason or pipeline_module.ABSTENTION_REASON_UNKNOWN}"
        if abstained else class_name
    )
    m._record_analysis(metric_class, elapsed * 1000)

    logger.info("Analysis completed")

    # Resolve the species fan-out. We started it in parallel with the
    # disease pipeline so this normally returns immediately. If the
    # caller already supplied a ``species`` query param (e.g. user
    # picked one earlier), it overrides the auto-detected scientific
    # name when looking up pet toxicity, but we keep the auto-detected
    # block in the response either way so the client can show the
    # match confidence.
    if species_task is not None and abstained:
        species_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await species_task
    elif species_task is not None:
        try:
            species_payload = await species_task
        except Exception:  # noqa: BLE001 — defensive; helper already swallows
            species_payload = None

    species_upstream_attempted = (
        species_payload.get("_upstream_attempted")
        if isinstance(species_payload, dict)
        and type(species_payload.get("_upstream_attempted")) is bool
        else None
    )
    if species_authorized:
        await m._release_metered_feature_if_no_upstream_attempt(
            request,
            feature="plant_identification",
            upstream_attempted=species_upstream_attempted,
        )
    if isinstance(species_payload, dict):
        species_payload.pop("_upstream_attempted", None)

    # The shared builder applies the abstention species suppression, the
    # toxicity lookup and the response shape. ``scan_id=None`` keeps the
    # cacheable payload free of per-response capabilities; they are minted
    # below from the species block the builder settled on.
    cached_response_payload = analysis_payload.build_analyze_payload(
        result,
        scan_id=None,
        species_payload=species_payload,
        species_override=species,
        served_family=served_family,
        requested_family=requested_family,
        family_fallback_reason=family_fallback_reason,
    )
    auto_best = (cached_response_payload["species"] or {}).get("best") or {}

    capture_pending = bool(
        m.TRAINING_CAPTURE_ENABLED
        and training_consent
        and not abstained
    )
    community_token, feedback_receipt = await issue_interaction_tokens(
        m,
        capture_pending=capture_pending,
        # A rejected image keeps its raw argmax in ``class_name`` but is not a
        # diagnosis: bind the publication proof to the hidden NAP class so an
        # abstained scan can never surface in the community feed as a disease.
        disease="not_a_plant" if abstained else class_name,
        confidence=round(confidence, 3),
        species=auto_best.get("scientific_name"),
        species_score=auto_best.get("score"),
        image_sha256=image_sha,
    )
    # Stash the freshly-built response under its (image_hash,
    # plantnet_lang) key so the NEXT identical upload from the same
    # locale short-circuits at the top of this handler.
    # Not when this caller's PlantNet allowance blanked the species block: the
    # cache would replay that blank to everyone uploading the same bytes.
    if (cached_response_payload.get("species") or {}).get("source") != "quota":
        analyze_cache_put(m, cache_key, cached_response_payload)
    response_payload = {
        **cached_response_payload,
        # This server-minted token is accepted only by community publication.
        "scan_id": community_token,
        # This unrelated 256-bit capability is accepted only by feedback.
        "feedback_receipt": feedback_receipt,
    }
    if capture_pending and feedback_receipt is not None:
        background_tasks.add_task(
            record_training_candidate, m,
            img.copy(),
            feedback_receipt,
            family=family,
            model_version=model_version,
            result=copy.deepcopy(result),
            consent_version=consent_version,
        )
    # Every user with the onboarding consent counts towards the worldwide
    # popular lists, with or without location: one anonymous species+class
    # tally per fresh, accepted diagnosis (repeats of a photo are served from
    # the cache above and are not counted again).
    if training_consent and not abstained:
        background_tasks.add_task(
            record_popular_contribution, auto_best.get("scientific_name"), class_name,
        )
    return JSONResponse(content=response_payload)


async def identify_admitted(
    m,
    request: Request,
    image: UploadFile,
    slot,
    *,
    client_ip: str,
    organ: str,
    locale: str,
) -> JSONResponse:
    """The body of ``/api/identify``, run while holding an inference slot."""
    if m.pipeline is None:
        m._record_error()
        raise HTTPException(status_code=503, detail="Server starting up, try again shortly")
    if not image.content_type or image.content_type not in m.ALLOWED_CONTENT_TYPES:
        m._record_error()
        raise HTTPException(status_code=400, detail="Unsupported image format")
    try:
        contents = await m._read_upload(image)
    except HTTPException:
        m._record_error()
        raise
    except Exception as exc:
        m._record_error()
        logger.warning("Identify upload read failed")
        raise HTTPException(status_code=400, detail="Could not read uploaded file") from exc
    try:
        m._validate_image_bytes(contents)
    except HTTPException:
        m._record_error()
        raise
    organ = (organ or "auto").strip().lower()
    if organ not in ("leaf", "flower", "fruit", "bark", "habit", "auto"):
        organ = "auto"
    plantnet_lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
    loop = asyncio.get_running_loop()

    # Decode and validate the pixels before sending anything to PlantNet. The
    # NAP filter is the same detector + classifier pipeline as /api/analyze,
    # so it must see the same EXIF-normalised RGB image and run on the same
    # bounded inference executor (never on the event loop or network pool).
    try:
        img = await loop.run_in_executor(None, m._open_image, contents)
    except HTTPException:
        m._record_error()
        raise
    except Exception as exc:
        m._record_error()
        logger.exception("Unexpected error while decoding /api/identify upload: %s", exc)
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc

    img_w, img_h = img.size
    if img_w > m.MAX_IMAGE_DIMENSION or img_h > m.MAX_IMAGE_DIMENSION:
        m._record_error()
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too large ({img_w}x{img_h}). "
                   f"Maximum dimension: {m.MAX_IMAGE_DIMENSION}px",
        )
    if min(img_w, img_h) < m.MIN_IMAGE_DIMENSION:
        m._record_error()
        raise HTTPException(
            status_code=400,
            detail=f"Image resolution too small ({img_w}x{img_h}). "
                   f"Minimum dimension: {m.MIN_IMAGE_DIMENSION}px - please retake the photo.",
        )

    try:
        nap_result = await asyncio.wait_for(
            loop.run_in_executor(m._get_inference_executor(), m.pipeline.analyze, img),
            timeout=m.INFERENCE_TIMEOUT,
        )
    except TimeoutError as exc:
        m._record_error()
        logger.error("NAP filter timed out after %.0fs for /api/identify", m.INFERENCE_TIMEOUT)
        raise HTTPException(
            status_code=504,
            detail=f"Analysis timed out after {int(m.INFERENCE_TIMEOUT)} seconds",
        ) from exc
    except Exception as exc:
        m._record_error()
        logger.exception("NAP filter crashed for /api/identify: %s", exc)
        raise HTTPException(status_code=500, detail="Internal inference error") from exc

    # The NAP gate was the only inference here; PlantNet waits slot-free.
    slot.release()
    if nap_result.get("class_name") == "not_a_plant":
        return JSONResponse(content={
            "available": False,
            "results": [],
            "source": "skipped_nap",
            "organ": organ,
        })

    # Re-encode from the already-decoded image: a direct API client must not be
    # able to bypass the Android uploader's sanitizer and hand PlantNet EXIF
    # (including GPS) that never needed to leave the device.
    try:
        external_image = await loop.run_in_executor(
            None,
            lambda: metadata_free_external_jpeg(m, img),
        )
    except HTTPException:
        m._record_error()
        raise
    except Exception as exc:
        m._record_error()
        logger.warning("Identify image validation failed")
        raise HTTPException(status_code=400, detail="Cannot read image file") from exc
    cached_result = await loop.run_in_executor(
        None,
        functools.partial(
            ai_services.plantnet_cache_lookup,
            external_image,
            organ=organ,
            lang=plantnet_lang,
        ),
    )
    identification_authorized = False
    if cached_result is None:
        # Per-IP allowance on PlantNet lookups (a cache hit spends none),
        # checked before the account's metered budget is touched.
        if not m._plantnet_quota.try_consume(client_ip):
            return JSONResponse(content={
                "available": False,
                "results": [],
                "source": "quota",
                "organ": organ,
            })
        if ai_services.plantnet_configured():
            await m._authorize_metered_feature(request, feature="plant_identification")
            identification_authorized = True
    try:
        result = cached_result
        if result is None:
            result = await loop.run_in_executor(
                m._get_net_executor(),
                lambda: ai_services.identify_species(
                    external_image,
                    organ=organ,
                    lang=plantnet_lang,
                ),
            )
    except HTTPException:
        m._record_error()
        raise
    upstream_attempted = (
        result.get("_upstream_attempted")
        if isinstance(result, dict)
        and type(result.get("_upstream_attempted")) is bool
        else None
    )
    if identification_authorized:
        await m._release_metered_feature_if_no_upstream_attempt(
            request,
            feature="plant_identification",
            upstream_attempted=upstream_attempted,
        )
    # Deep-copy result before merging so a cache-hit return from
    # ``identify_species`` (which now also deep-copies, but defence in
    # depth is cheap) doesn't share its nested ``results`` /
    # ``common_names`` lists with the JSON response — middleware mutation
    # would otherwise corrupt the PlantNet LRU entry for the next reader.
    public_result = copy.deepcopy(result)
    public_result.pop("_upstream_attempted", None)
    return JSONResponse(content={**public_result, "organ": organ})


