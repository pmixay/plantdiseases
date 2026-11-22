"""The diagnosis flow: analyze, identify, symptom triage, progression, feedback.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# Collaborator modules (never names patched on ``main``) are imported here;
# flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import analysis, analysis_payload, symptoms
except ImportError:
    import analysis
    import analysis_payload
    import symptoms


class SymptomDiagnoseRequest(BaseModel):
    # question_id -> option_id (see symptoms.SYMPTOM_WEIGHTS). Kept small and
    # length-capped — this is an untrusted client payload.
    answers: dict[str, str] = Field(default_factory=dict)
    locale: str = Field(default="en", max_length=8)

    @field_validator("answers")
    @classmethod
    def _validate_answers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("too many answers")
        for k, v in value.items():
            if len(k) > 32 or len(v) > 32:
                raise ValueError("answer id too long")
        return value


# The app sends its 12 newest scans; the cap leaves room for that to grow
# while bounding the work a single request can hand the event loop.
PROGRESSION_MAX_HISTORY = 50


class ProgressionRequest(BaseModel):
    """Time-ordered list of scans (oldest first) for the disease-
    progression sparkline + trend the plant-detail screen renders."""

    history: list[dict] = Field(..., max_length=PROGRESSION_MAX_HISTORY)


def register(router: APIRouter, m) -> None:
    @router.post("/analyze")
    async def analyze_image(
        request: Request,
        background_tasks: BackgroundTasks,
        image: UploadFile = File(...),
        species: str | None = Query(default=None, max_length=128),
        locale: str = Query(default="en", max_length=16),
        family: str = Query(default=m.DEFAULT_FAMILY, max_length=16),
        training_consent: bool = Query(default=False),
        training_consent_version: str | None = Query(default=None, max_length=32),
    ):
        """Analyse a plant image for diseases (two-stage pipeline).

        Accepts JPEG, PNG, WebP, or BMP images up to 10 MB.
        Returns bilingual (EN/RU) disease diagnosis with treatment advice,
        detected regions, a conservative compatibility severity placeholder, and
        the top-k class probabilities. ``locale`` (default "en") is forwarded to PlantNet
        so the species ``common_name`` is localised in the response.

        ``family`` selects the model tier (aqua | gamma | frontier). ``gamma``
        is the default; ``aqua`` is normally answered on-device but is served
        by gamma here for online parity; ``frontier`` requires the operator to
        have enabled and provisioned it (otherwise a graceful ``available:false``
        payload is returned, never a 5xx).

        Admission-controlled: a request that finds the server full gets 503 +
        ``Retry-After`` before its upload is read (see ``_inference_slot``), and
        each IP gets ``ANALYZE_DAILY_QUOTA`` analyses a day (429 past it).
        """
        async with m._inference_slot(request) as slot:
            # Taken once admitted, so a busy 503 (and the app's retry of it)
            # does not spend the caller's allowance.
            m._require_daily_quota(m._analyze_quota, request)
            return await analysis.analyze_image_admitted(
                m, request, background_tasks, image, slot,
                client_ip=m._client_key(m._client_ip(request)),
                species=species, locale=locale, family=family,
                training_consent=training_consent,
                training_consent_version=training_consent_version,
            )


    @router.post("/identify")
    async def identify_endpoint(
        request: Request,
        image: UploadFile = File(...),
        organ: str = "leaf",
        locale: str = "en",
    ):
        """PlantNet-backed plant species identification.

        Returns up to 5 candidate species with confidence scores. When the
        PlantNet API key is missing we still respond 200 OK with
        ``available: false`` so the client can render a graceful state
        instead of an error toast. Cached server-side by SHA-256(image) +
        organ + locale to keep the daily PlantNet quota intact.

        ``locale`` is forwarded to PlantNet's ``lang`` query param so the
        common names come back localised (e.g. "Каучуковое дерево" on
        ru-RU instead of "Rubber tree").

        The same local NAP gate used by ``/api/analyze`` runs before PlantNet.
        A ``not_a_plant`` verdict returns a graceful ``available: false``
        payload and does not spend a PlantNet identification request.

        Shares ``/api/analyze``'s admission control (``_inference_slot``): the NAP
        gate runs on the same inference executor. Each IP gets
        ``IDENTIFY_DAILY_QUOTA`` calls a day (429 past it), and its PlantNet calls
        count against ``PLANTNET_DAILY_QUOTA_PER_IP`` (``available: false``,
        ``source: "quota"`` past it).
        """
        async with m._inference_slot(request) as slot:
            m._require_daily_quota(m._identify_quota, request)
            return await analysis.identify_admitted(
                m, request, image, slot,
                client_ip=m._client_key(m._client_ip(request)), organ=organ, locale=locale,
            )


    @router.post("/symptom-diagnose")
    async def symptom_diagnose_endpoint(payload: SymptomDiagnoseRequest):
        """No-photo symptom triage — the fallback when the camera/ML pipeline
        can't get a confident read.

        Scores the answered questionnaire against a transparent weight table
        (``symptoms.py``) and returns a ranked shortlist of likely conditions,
        each enriched with its full bilingual care card. ``low_confidence`` is
        set when the answers are too sparse/weak to trust, so the client can
        nudge the user to take a photo for a more reliable diagnosis. Always
        returns 200 with a usable payload — an empty questionnaire just yields
        an empty shortlist + the ``unknown`` guidance card.
        """
        triage = symptoms.diagnose(payload.answers)
        results = []
        for item in triage["results"]:
            card = analysis_payload.compact_care_card(item["class"])
            card["score"] = item["score"]
            card["confidence"] = item["confidence"]
            card["rank"] = item["rank"]
            results.append(card)
        # When nothing scored, hand back the generic "unknown" guidance card so
        # the screen is never blank — and flag low confidence.
        if not results:
            fallback = analysis_payload.compact_care_card("unknown")
            fallback["score"] = 0.0
            fallback["confidence"] = 0.0
            fallback["rank"] = 1
            results = [fallback]
        return JSONResponse(
            content={
                "results": results,
                "answered": triage["answered"],
                "low_confidence": triage["low_confidence"] or not triage["results"],
            }
        )


    @router.post("/progression")
    async def progression_endpoint(payload: ProgressionRequest):
        """Disease-progression analysis. Returns a 5-point severity
        sparkline + a trend slope across the supplied history (oldest
        first). An empty list is allowed and yields a zero-padded
        baseline so the client can render an "awaiting data" state
        without special-casing. At most ``PROGRESSION_MAX_HISTORY`` entries
        (422 past it), so the computation stays a few microseconds of event-loop
        time."""
        return JSONResponse(content=m.ai_services.compare_progression_history(payload.history))


    @router.post("/feedback")
    async def feedback_endpoint(payload: m.FeedbackRequest, request: Request):
        """Apply one structured correction through a short-lived receipt.

        The receipt is independent from accounts, community publication tokens,
        and client scan identifiers. Only its digest exists at rest. The live
        connection address is used for an in-process quota and is never written.
        """
        client_ip = m._client_ip(request)
        if not m._consume_daily_write("feedback", client_ip, m.FEEDBACK_DAILY_QUOTA):
            m.logger.warning("Feedback daily quota reached")
            raise HTTPException(
                status_code=429,
                detail="Daily feedback quota reached. Try again tomorrow.",
            )
        out_of_head: bool | None = None
        if payload.corrected_class:
            out_of_head = payload.corrected_class not in m._FEEDBACK_IN_HEAD_CLASSES
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: m.training_store.apply_feedback(
                    payload.feedback_receipt,
                    correct=payload.correct,
                    corrected_class=payload.corrected_class,
                    correction_reason=payload.reason,
                    out_of_head=out_of_head,
                ),
            )
        except m.training_store.TrainingStoreError as exc:
            if exc.code == "feedback_not_ready":
                raise HTTPException(status_code=409, detail="feedback_not_ready") from None
            if exc.code == "feedback_receipt_used":
                raise HTTPException(status_code=409, detail="feedback_receipt_used") from None
            raise HTTPException(status_code=422, detail="feedback_receipt_invalid") from None
        except Exception as exc:  # noqa: BLE001
            m.logger.error("Failed to record structured feedback")
            raise HTTPException(status_code=500, detail="Failed to record feedback") from exc
        m.logger.info("Structured feedback recorded")
        return JSONResponse(content={"ok": True, "updated": result.updated})


    @router.post("/feedback/image")
    async def feedback_image_upload(
        request: Request,
        feedback_receipt: str = Form(...),
        correct: bool = Form(...),
        corrected_class: str | None = Form(None),
        reason: str | None = Form(None),
        image: UploadFile = File(...),
    ):
        """Apply feedback and optionally retain a detector-backed leaf crop.

        Full frames are never stored by this endpoint. If the detector cannot
        provide a bounded leaf crop, the upload is rejected without retaining
        pixels. Re-encoding strips metadata before hashes are calculated.
        """
        try:
            payload = m.FeedbackRequest(
                feedback_receipt=feedback_receipt,
                correct=correct,
                corrected_class=corrected_class,
                reason=reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_feedback") from exc

        client_ip = m._client_ip(request)
        if not m._consume_daily_write("feedback_image", client_ip, m.FEEDBACK_DAILY_QUOTA):
            m.logger.warning("Feedback image daily quota reached")
            raise HTTPException(
                status_code=429,
                detail="Daily feedback quota reached. Try again tomorrow.",
            )
        image_bytes = await m._read_upload(image)
        # Apply the same byte-signature allowlist as the analysis endpoint before
        # decoding or invoking the privacy screen. Content-Type is attacker-owned.
        m._validate_image_bytes(image_bytes)
        img = m._open_image(image_bytes)
        if m.pipeline is None or not getattr(m.pipeline, "detector", None):
            raise HTTPException(status_code=503, detail="privacy_screen_unavailable")
        loop = asyncio.get_running_loop()
        try:
            detector_result = await asyncio.wait_for(
                loop.run_in_executor(m._get_inference_executor(), m.pipeline.detector.detect, img),
                timeout=m.INFERENCE_TIMEOUT,
            )
        except (AttributeError, asyncio.TimeoutError):
            raise HTTPException(status_code=503, detail="privacy_screen_unavailable") from None
        except Exception as exc:  # noqa: BLE001
            m.logger.warning("Feedback image privacy screen failed")
            raise HTTPException(status_code=422, detail="unsafe_training_image") from exc

        safe_crop = m.training_store.extract_safe_leaf_crop(
            img, {"regions": list((detector_result or {}).get("detections") or [])}
        )
        if safe_crop is None:
            raise HTTPException(status_code=422, detail="unsafe_training_image")
        crop, _area_buckets = safe_crop
        try:
            sanitized = await loop.run_in_executor(
                None, m.training_store.sanitize_training_image, crop
            )
            out_of_head = (
                payload.corrected_class not in m._FEEDBACK_IN_HEAD_CLASSES
                if payload.corrected_class
                else None
            )
            result = await loop.run_in_executor(
                None,
                lambda: m.training_store.store_feedback_upload(
                    payload.feedback_receipt,
                    sanitized,
                    correct=payload.correct,
                    corrected_class=payload.corrected_class,
                    correction_reason=payload.reason,
                    out_of_head=out_of_head,
                    consent_version=m.TRAINING_CONSENT_VERSION,
                ),
            )
        except m.training_store.TrainingStoreError as exc:
            if exc.code == "feedback_not_ready":
                raise HTTPException(status_code=409, detail="feedback_not_ready") from None
            if exc.code == "feedback_receipt_used":
                raise HTTPException(status_code=409, detail="feedback_receipt_used") from None
            raise HTTPException(status_code=422, detail="feedback_receipt_invalid") from None
        except Exception as exc:  # noqa: BLE001
            m.logger.error("Failed to store privacy-screened feedback image")
            raise HTTPException(status_code=500, detail="Failed to record feedback") from exc

        m.logger.info("Privacy-screened feedback image processed")
        return JSONResponse(
            content={
                "ok": True,
                "updated": result.updated,
                "stored_image": result.stored_image,
            }
        )
