"""The /api/analyze response shape: care cards, abstention presentation, payload builder."""


# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import app_version
except ImportError:
    import app_version
try:
    from . import diseases_data
except ImportError:
    import diseases_data
try:
    from . import pipeline as pipeline_module
except ImportError:
    import pipeline as pipeline_module


# The two physiological-stress classes whose care advice genuinely
# diverges — under-watering (``leaf_scorch_underwatering``) says "water me"
# / fix humidity & salts, nutrient deficiency says "feed me". When the
# conformal set can't separate them (the kept-both taxonomy decision), the
# result screen shows BOTH care cards instead of forcing the user to guess.
PHYSIOLOGICAL_CARE_PAIR = frozenset(
    {"leaf_scorch_underwatering", "nutrient_deficiency"}
)


def compact_care_card(cls: str) -> dict:
    """Bilingual care card for one class, used in the dual-care prediction
    set so the client can render a second full care card without a second
    round-trip."""
    info = diseases_data.DISEASES_DATABASE.get(cls, diseases_data.DISEASES_DATABASE["unknown"])
    return {
        "class": cls,
        "name_en": info.get("name_en", cls.replace("_", " ").title()),
        "name_ru": info.get("name_ru", cls),
        "description": info.get("description_en", ""),
        "description_ru": info.get("description_ru", ""),
        "treatment": info.get("treatment_en", []),
        "treatment_ru": info.get("treatment_ru", []),
        "prevention": info.get("prevention_en", []),
        "prevention_ru": info.get("prevention_ru", []),
    }


def attach_dual_care(prediction_set: dict) -> None:
    """Attach both physiological care cards in-place (P0(b)).

    No-op unless the conformal set contains BOTH ``leaf_scorch_underwatering``
    and ``nutrient_deficiency`` — the one ambiguous pair whose care advice
    diverges enough to warrant showing two cards. Scoped this tightly so
    every other prediction set keeps a lean payload.
    """
    classes = prediction_set.get("classes") or []
    if not PHYSIOLOGICAL_CARE_PAIR.issubset(set(classes)):
        return
    prediction_set["dual_care"] = True
    prediction_set["care_cards"] = [
        compact_care_card(cls)
        for cls in classes
        if cls in PHYSIOLOGICAL_CARE_PAIR
    ]


def apply_abstention_presentation(payload: dict) -> dict:
    """Apply the public rejection contract without changing diagnostics.

    ``class_name`` / ``confidence`` / ``all_probs`` / ``top_k`` remain the raw
    probability-backed classifier diagnostics. OOD and quality rejections are
    not accepted diagnoses, so every downstream claim (disease care, species,
    pet toxicity, conformal set and disease flag) is neutralised. A natural
    trained-head ``not_a_plant`` keeps its established NAP copy.
    """
    abstained, reason = pipeline_module._derive_abstention(payload)
    payload["abstained"] = abstained
    payload["abstention_reason"] = reason
    payload["verdict_status"] = "abstained" if abstained else "accepted"
    if not abstained:
        return payload

    detection = dict(payload.get("detection") or {})
    detection["is_diseased"] = False
    payload["detection"] = detection
    payload["prediction_set"] = None
    payload["is_healthy"] = False
    payload["severity"] = "low"
    payload["toxic_to_pets"] = False
    payload["toxic_species_list"] = []
    payload["toxicity_source"] = "unknown"
    payload["species"] = {
        "available": False,
        "source": (
            "skipped_nap" if reason == pipeline_module.ABSTENTION_REASON_NAP
            else "skipped_abstained"
        ),
        "best": None,
        "candidates": [],
    }

    if reason != pipeline_module.ABSTENTION_REASON_NAP:
        neutral = diseases_data.DISEASES_DATABASE["unknown"]
        payload["disease_name"] = neutral["name_en"]
        payload["disease_name_ru"] = neutral["name_ru"]
        payload["description"] = neutral["description_en"]
        payload["description_ru"] = neutral["description_ru"]
        payload["treatment"] = list(neutral["treatment_en"])
        payload["treatment_ru"] = list(neutral["treatment_ru"])
        # Prevention/care advice is a diagnosis-dependent claim. Keep only the
        # explicit retake actions above until an image is accepted.
        payload["prevention"] = []
        payload["prevention_ru"] = []
    return payload


def build_analyze_payload(
    result: dict,
    *,
    scan_id: str | None,
    species_payload: dict | None,
    species_override: str | None,
    served_family: str,
    requested_family: str,
    family_fallback_reason: str | None,
) -> dict:
    """Assemble the ``/api/analyze`` response from one pipeline ``result``.

    Pure — no I/O, no awaits — so the response shape (the bilingual top-k, the
    advisory conformal set and its dual care cards, the abstention-driven
    species suppression, the toxicity fall-through and the abstention
    presentation) is unit-testable without a TestClient. The handler keeps
    everything that talks to the outside world: inference, the PlantNet wait,
    minting the per-response tokens, the cache and the scan recording.
    ``scan_id`` is omitted from the payload when ``None``.

    ``species_payload`` is the resolved PlantNet block (``None`` when the
    handler cancelled it); ``species_override`` is the ``?species=`` hint,
    which drives the toxicity lookup only.
    """
    class_name = result["class_name"]
    confidence = result["confidence"]
    all_probs = result.get("all_probs", {})
    warnings = list(result.get("warnings", []))
    top_k = list(result.get("top_k", []))
    abstained, abstention_reason = pipeline_module._derive_abstention(result)
    disease_info = diseases_data.DISEASES_DATABASE.get(class_name, diseases_data.DISEASES_DATABASE["unknown"])

    rounded_probs = {k: round(v, 4) for k, v in all_probs.items()}

    detection = result["detection"]

    # Annotate top_k with the bilingual display name so the client can
    # render "Leaf mould - 6%" (or its RU variant) without a second
    # lookup table.
    top_k_localised = []
    for entry in top_k:
        cls = entry.get("class", "")
        info = diseases_data.DISEASES_DATABASE.get(cls, diseases_data.DISEASES_DATABASE["unknown"])
        top_k_localised.append({
            "class": cls,
            "confidence": entry.get("confidence", 0.0),
            "name_en": info.get("name_en", cls.replace("_", " ").title()),
            "name_ru": info.get("name_ru", cls),
            "is_healthy": bool(info.get("is_healthy", False)),
        })

    # `is_healthy` mirrors the displayed class name so the client's
    # status badge can never contradict the diagnosis text. Uncertainty
    # is conveyed by the `warnings` array (`uncertain_healthy`,
    # `disease_suspected:<class>`) and the top-k alternatives card,
    # not by silently flipping this boolean.
    is_healthy_effective = (
        not abstained and bool(disease_info.get("is_healthy", False))
    )

    # Conformal prediction set: classes the model can't rule out at the
    # configured coverage level. Annotated with bilingual names so the
    # UI can render an advisory "leaf_spot OR viral_disease" set with its
    # nominal target clearly distinguished from measured coverage.
    # without a second lookup table. ``None`` when calibration data
    # isn't yet available — clients must treat it as optional.
    prediction_set = None if abstained else result.get("prediction_set")
    if prediction_set is not None:
        # Calibration-row coverage is not an independent validation. Keep the
        # set advisory until a locked, family-independent served-path test
        # establishes coverage for this exact graph + detector/crop/vote
        # contract; no per-class-count heuristic can substitute for that.
        prediction_set = {
            **prediction_set,
            "advisory": True,
            "items": [
                {
                    "class": cls,
                    "name_en": diseases_data.DISEASES_DATABASE.get(cls, diseases_data.DISEASES_DATABASE["unknown"]).get(
                        "name_en", cls.replace("_", " ").title()
                    ),
                    "name_ru": diseases_data.DISEASES_DATABASE.get(cls, diseases_data.DISEASES_DATABASE["unknown"]).get(
                        "name_ru", cls
                    ),
                }
                for cls in prediction_set["classes"]
            ],
        }
        # Dual care cards (P0(b), kept-both taxonomy): when the set can't
        # separate the two physiological-stress classes, their care advice
        # diverges enough ("water me" vs "feed me") that we surface BOTH
        # full care cards rather than only the top-1's.
        attach_dual_care(prediction_set)

    # PlantNet on a non-plant photo always returns a low-quality guess
    # ("Phaseolus vulgaris" because there was some green pixel noise)
    # which is actively misleading on the result screen. When the
    # pipeline lands on ``not_a_plant`` we suppress the species block
    # entirely — the LRU cache in ``identify_species`` keeps the API
    # call from being wasted on a repeat upload of the same image.
    if abstained:
        species_payload = {
            "available": False,
            "source": (
                "skipped_nap"
                if abstention_reason == pipeline_module.ABSTENTION_REASON_NAP
                else "skipped_abstained"
            ),
            "best": None, "candidates": [],
        }

    # Pet-toxicity surface — resolves from the explicit ``species``
    # query param when supplied, otherwise falls back to the auto-
    # detected best match. Without either we return
    # ``source="unknown"`` and the client renders an "unconfirmed"
    # copy rather than falsely reassuring.
    auto_best = (species_payload or {}).get("best") or {}
    toxicity_species = None if abstained else (species_override or auto_best.get("scientific_name"))
    toxicity = diseases_data.lookup_pet_toxicity(toxicity_species) if toxicity_species else {
        "toxic_to_pets": False, "species_list": [], "source": "unknown",
    }

    response_payload = {
        # Probability-backed classifier argmax. Rejection gates never replace
        # it: clients use ``abstained`` / ``abstention_reason`` to decide
        # whether this diagnostic may be presented as an accepted diagnosis.
        "class_name": class_name,
        # Which model tier actually produced this verdict. Lets the client
        # confirm it got the family it asked for (and fall back gracefully if
        # a future server downgrades the request).
        "family": served_family,
        "requested_family": requested_family,
        "family_fallback_reason": family_fallback_reason,
        "disease_name": disease_info["name_en"],
        "disease_name_ru": disease_info["name_ru"],
        "confidence": round(confidence, 3),
        "description": disease_info["description_en"],
        "description_ru": disease_info["description_ru"],
        "treatment": disease_info["treatment_en"],
        "treatment_ru": disease_info["treatment_ru"],
        "prevention": disease_info["prevention_en"],
        "prevention_ru": disease_info["prevention_ru"],
        "is_healthy": is_healthy_effective,
        "detection": detection,
        "severity": result.get("severity", "low"),
        "all_probs": rounded_probs,
        "top_k": top_k_localised,
        "warnings": warnings,
        "abstained": abstained,
        "abstention_reason": abstention_reason,
        "verdict_status": "abstained" if abstained else "accepted",
        "prediction_set": prediction_set,
        "toxic_to_pets": toxicity["toxic_to_pets"],
        "toxic_species_list": toxicity["species_list"],
        "toxicity_source": toxicity["source"],
        "species": species_payload,
        "elapsed_ms": result["elapsed_ms"],
        "server_version": app_version.APP_VERSION,
        # Set to true only when the response is replayed from the
        # SHA-256 cache above; first-time scans get false here.
        "is_repeat": False,
    }
    if scan_id is not None:
        # Server-minted token for this one response. The handler passes None
        # for the cacheable payload and adds its own capabilities afterwards,
        # so a token is never replayed from the diagnosis cache.
        response_payload["scan_id"] = scan_id
    return apply_abstention_presentation(response_payload)


