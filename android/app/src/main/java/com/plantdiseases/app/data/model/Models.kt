package com.plantdiseases.app.data.model

import com.google.gson.annotations.SerializedName
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.nullable
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull

// NOTE: kotlinx.serialization is the wire format — every HTTP endpoint runs
// through the Ktor client and no Retrofit remains anywhere in the app. The
// models stay dual-annotated because Gson is still live *off* the network:
// @SerializedName is what keeps the Room field JSON (ScanEntity's
// `prediction_set` / `regions`), util/ResponseCache and the ResultActivity
// intent payloads readable — including rows and cache files already on disk.
// @Serializable + @SerialName drive the Ktor decode. The two annotations
// mirror each other 1:1; dropping either breaks something silently under R8.

/** Single bounding box returned by the YOLOv8 detector (Stage 1). */
@Serializable
data class DetectionRegion(
    @SerializedName("x") @SerialName("x") val x: Int,
    @SerializedName("y") @SerialName("y") val y: Int,
    @SerializedName("width") @SerialName("width") val width: Int,
    @SerializedName("height") @SerialName("height") val height: Int,
    @SerializedName("class") @SerialName("class") val className: String? = null,
    @SerializedName("confidence") @SerialName("confidence") val confidence: Float? = null
)

/** Stage 1 detection result — list of boxes plus the one Stage 2 used.
 *
 * ``image_width`` / ``image_height`` are the pixel dims of the image
 * the server actually saw (post-upload-decode). The bbox coordinates
 * are in that space, so any overlay renderer must scale against these
 * dims rather than against the raw camera file's bitmap (which is
 * typically larger and may be EXIF-rotated). */
@Serializable
data class Detection(
    @SerializedName("is_diseased") @SerialName("is_diseased") val isDiseased: Boolean = false,
    @SerializedName("detector_confidence") @SerialName("detector_confidence") val detectorConfidence: Float = 0f,
    @SerializedName("image_width") @SerialName("image_width") val imageWidth: Int = 0,
    @SerializedName("image_height") @SerialName("image_height") val imageHeight: Int = 0,
    @SerializedName("regions") @SerialName("regions") val regions: List<DetectionRegion> = emptyList(),
    @SerializedName("primary_region") @SerialName("primary_region") val primaryRegion: DetectionRegion? = null
)

/** Single entry in the server-provided top-k candidate list. */
@Serializable
data class TopKEntry(
    @SerializedName("class") @SerialName("class") val className: String = "",
    @SerializedName("confidence") @SerialName("confidence") val confidence: Float = 0f,
    @SerializedName("name_en") @SerialName("name_en") val nameEn: String? = null,
    @SerializedName("name_ru") @SerialName("name_ru") val nameRu: String? = null,
    @SerializedName("is_healthy") @SerialName("is_healthy") val isHealthy: Boolean? = null
)

/** One class inside a conformal prediction set, with bilingual labels. */
@Serializable
data class PredictionSetItem(
    @SerializedName("class") @SerialName("class") val className: String = "",
    @SerializedName("name_en") @SerialName("name_en") val nameEn: String? = null,
    @SerializedName("name_ru") @SerialName("name_ru") val nameRu: String? = null,
)

/**
 * Conformal prediction set — classes the model cannot rule out at the nominal
 * `1-alpha` target. The server currently marks sets advisory; the number is not
 * delivered coverage until a locked independent evaluation exists.
 */
@Serializable
data class PredictionSet(
    @SerializedName("classes") @SerialName("classes") val classes: List<String> = emptyList(),
    @SerializedName("coverage") @SerialName("coverage") val coverage: Float = 0f,
    @SerializedName("method") @SerialName("method") val method: String? = null,
    @SerializedName("items") @SerialName("items") val items: List<PredictionSetItem> = emptyList(),
    /**
     * True until the conformal calibration set is large enough for the
     * coverage figure to carry a real statistical guarantee. When
     * `advisory`, the UI must render the set as "other candidates to
     * consider" rather than claiming "<coverage>% guaranteed coverage".
     */
    @SerializedName("advisory") @SerialName("advisory") val advisory: Boolean = true,
    // The server also emits `dual_care` / `care_cards` for the ambiguous
    // under-watering vs nutrient-deficiency pair. No screen renders them, so
    // they are not modelled here; ignoreUnknownKeys drops them harmlessly.
    // Re-add both fields alongside the UI that would show the two cards.
)

/** Response from the server after analyzing an image */
@Serializable
data class AnalysisResponse(
    // The server uses HTTP 200 + available=false for a configured model tier
    // that is currently unavailable. Missing on normal/legacy diagnoses, so
    // true is the backward-compatible default.
    @SerializedName("available") @SerialName("available") val available: Boolean = true,
    @SerializedName("reason") @SerialName("reason") val unavailableReason: String? = null,
    @SerializedName("message") @SerialName("message") val availabilityMessage: String? = null,
    // Probability-backed classifier argmax (e.g. ``leaf_spot``). Rejection
    // gates deliberately preserve this diagnostic; [abstained] says whether
    // it may be presented to the user as an accepted diagnosis. The local
    // result UI uses it for structured labels; the feedback endpoint resolves
    // its own prediction through the opaque, one-use receipt and never trusts
    // an echoed client value.
    @SerializedName("class_name") @SerialName("class_name") val classKey: String? = null,
    // Gson does not honour Kotlin non-null types, so missing/partial
    // server responses would otherwise inject 0f / null silently (and
    // NPE on first access for reference types). Give every
    // always-expected field a safe default so older persisted JSON can be
    // inspected without crashing. The network repository separately applies
    // [diagnosticContractViolation] and rejects a partial HTTP response before
    // it can be shown or saved as a diagnosis.
    @SerializedName("disease_name") @SerialName("disease_name") val diseaseName: String = "",
    @SerializedName("disease_name_ru") @SerialName("disease_name_ru") val diseaseNameRu: String = "",
    @SerializedName("confidence") @SerialName("confidence") val confidence: Float = 0f,
    @SerializedName("description") @SerialName("description") val description: String = "",
    @SerializedName("description_ru") @SerialName("description_ru") val descriptionRu: String = "",
    @SerializedName("treatment") @SerialName("treatment") val treatment: List<String> = emptyList(),
    @SerializedName("treatment_ru") @SerialName("treatment_ru") val treatmentRu: List<String> = emptyList(),
    @SerializedName("prevention") @SerialName("prevention") val prevention: List<String> = emptyList(),
    @SerializedName("prevention_ru") @SerialName("prevention_ru") val preventionRu: List<String> = emptyList(),
    @SerializedName("is_healthy") @SerialName("is_healthy") val isHealthy: Boolean = false,
    // Compatibility field. Current bundles have no lesion/severity target, so
    // the server conservatively returns ``low`` rather than deriving extent
    // from leaf-box area or class confidence.
    @SerializedName("severity") @SerialName("severity") val severity: String? = null,
    @SerializedName("detection") @SerialName("detection") val detection: Detection? = null,
    @SerializedName("all_probs") @SerialName("all_probs") val allProbs: Map<String, Float>? = null,
    @SerializedName("top_k") @SerialName("top_k") val topK: List<TopKEntry>? = null,
    @SerializedName("warnings") @SerialName("warnings") val warnings: List<String>? = null,
    @SerializedName("abstained") @SerialName("abstained") val abstained: Boolean = false,
    @SerializedName("abstention_reason") @SerialName("abstention_reason") val abstentionReason: String? = null,
    @SerializedName("verdict_status") @SerialName("verdict_status") val verdictStatus: String? = null,
    @SerializedName("prediction_set") @SerialName("prediction_set") val predictionSet: PredictionSet? = null,
    /**
     * Toxic-to-pets surface. `toxicitySource` is `"db"` when the
     * server matched the species against its ASPCA-derived table,
     * `"unknown"` when we couldn't confirm — UI must NOT falsely
     * reassure on unknown.
     */
    @SerializedName("toxic_to_pets") @SerialName("toxic_to_pets") val toxicToPets: Boolean = false,
    @SerializedName("toxic_species_list") @SerialName("toxic_species_list") val toxicSpeciesList: List<String> = emptyList(),
    @SerializedName("toxicity_source") @SerialName("toxicity_source") val toxicitySource: String = "unknown",
    /** Auto-detected species block from the server-side PlantNet
     *  fan-out. ``null`` when the upstream failed; ``available=false``
     *  when PlantNet isn't configured on this server. */
    @SerializedName("species") @SerialName("species") val species: SpeciesBlock? = null,
    @SerializedName("elapsed_ms") @SerialName("elapsed_ms") val elapsedMs: Float? = null,
    // Server-minted community publication capability. It is deliberately
    // distinct from the feedback receipt below and cannot authorize feedback.
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String? = null,
    // Short-lived, one-use capability for anonymous structured feedback.
    // It is distinct from the community scan token and cannot authorize any
    // other operation.
    @SerializedName("feedback_receipt")
    @SerialName("feedback_receipt")
    val feedbackReceipt: String? = null,
    // Same-photo dedup signal from /api/analyse. ``isRepeat=true``
    // means the server replayed a previously-computed response from
    // its SHA-256 cache; the verdict is byte-identical to whatever
    // was returned for the first upload of this exact image bytes.
    // ``cachedAt`` is the epoch seconds of that first upload — the
    // client can render "you saw this one earlier today" without
    // pretending the diagnosis is fresh.
    @SerializedName("is_repeat") @SerialName("is_repeat") val isRepeat: Boolean = false,
    @SerializedName("cached_at") @SerialName("cached_at") val cachedAt: Double? = null,
    // Model tier that produced this verdict (aqua | gamma | frontier).
    // Echoed back by the server so the client can confirm which family
    // answered (and detect a server-side downgrade). On-device Aqua
    // results set this locally. Defaults to gamma for older responses.
    @SerializedName("family") @SerialName("family") val family: String = "gamma",
    @SerializedName("requested_family") @SerialName("requested_family") val requestedFamily: String? = null,
    @SerializedName("family_fallback_reason") @SerialName("family_fallback_reason") val familyFallbackReason: String? = null,
)

/** Stable rejection reasons shared by API, persistence and UI policy. */
object AnalysisAbstentionReasons {
    const val OOD = "ood"
    const val IMAGE_QUALITY = "image_quality"
    const val NOT_A_PLANT = "not_a_plant"
    const val UNKNOWN = "unknown"
}

/**
 * Effective rejection reason with backward-compatible warning derivation.
 *
 * Old cached responses and Room rows only carry ``nap_ood_gated`` /
 * ``blurry_image`` warnings. Those markers deliberately fail closed even when
 * a deserialiser defaulted the newer ``abstained`` field to false.
 */
fun AnalysisResponse.effectiveAbstentionReason(): String? {
    val warningSet = warnings.orEmpty().map { it.trim().lowercase() }.toSet()
    if ("nap_ood_gated" in warningSet) return AnalysisAbstentionReasons.OOD
    if ("blurry_image" in warningSet) return AnalysisAbstentionReasons.IMAGE_QUALITY
    abstentionReason?.trim()?.lowercase()?.takeIf { it.isNotBlank() }?.let { return it }
    if (classKey == AnalysisAbstentionReasons.NOT_A_PLANT) {
        return AnalysisAbstentionReasons.NOT_A_PLANT
    }
    if (abstained || verdictStatus.equals("abstained", ignoreCase = true)) {
        return AnalysisAbstentionReasons.UNKNOWN
    }
    return null
}

fun AnalysisResponse.isEffectivelyAbstained(): Boolean =
    effectiveAbstentionReason() != null

/**
 * Return the first wire-contract violation in an otherwise available analysis.
 *
 * Defaults keep old persisted JSON readable, but they must not turn a partial
 * HTTP 200 (for example ``{}``) into an accepted diagnosis. Every accepted
 * verdict, natural not-a-plant verdict, and OOD abstention therefore carries
 * one internally consistent probability-backed diagnostic. Image-quality
 * rejection is the deliberate exception: it happens before inference and must
 * carry no invented classifier distribution.
 */
fun AnalysisResponse.diagnosticContractViolation(): String? {
    if (!available) return null // Availability has its own typed repository error.

    if (effectiveAbstentionReason() == AnalysisAbstentionReasons.IMAGE_QUALITY) {
        val qualityClass = classKey?.trim().orEmpty()
        if (qualityClass.isNotEmpty() && qualityClass != AnalysisAbstentionReasons.UNKNOWN) {
            return "image-quality rejection carries a classifier class"
        }
        if (!confidence.isFinite() || confidence != 0f) {
            return "image-quality rejection confidence must be zero"
        }
        if (!allProbs.isNullOrEmpty() || !topK.isNullOrEmpty()) {
            return "image-quality rejection carries classifier probabilities"
        }
        return null
    }

    val key = classKey ?: return "class_name is missing"
    if (key.isBlank() || key != key.trim() || key !in CANONICAL_ANALYSIS_CLASS_KEYS) {
        return "class_name is not a known class key"
    }
    if (diseaseName.isBlank()) return "disease_name is missing"
    if (!confidence.isFinite() || confidence !in 0f..1f) {
        return "confidence is outside [0,1]"
    }

    val probs = allProbs ?: return "all_probs is missing"
    if (probs.isEmpty()) return "all_probs is empty"
    if (probs.keys != CANONICAL_ANALYSIS_CLASS_KEYS) {
        return "all_probs keys do not match the canonical taxonomy"
    }
    if (probs.any { (name, probability) ->
            !isWellFormedClassKey(name) || !isUnitProbability(probability)
        }
    ) {
        return "all_probs contains an invalid class or probability"
    }
    val probabilitySum = probs.values.sum()
    if (!probabilitySum.isFinite() || kotlin.math.abs(probabilitySum - 1f) > 0.005f) {
        return "all_probs is not a probability distribution"
    }
    val classProbability = probs[key] ?: return "class_name is absent from all_probs"
    val maximumProbability = probs.values.maxOrNull()
        ?: return "all_probs is empty"
    if (kotlin.math.abs(classProbability - maximumProbability) > PROBABILITY_WIRE_TOLERANCE) {
        return "class_name is not the all_probs argmax"
    }
    if (kotlin.math.abs(confidence - classProbability) > PROBABILITY_WIRE_TOLERANCE) {
        return "confidence disagrees with all_probs"
    }

    val candidates = topK ?: return "top_k is missing"
    if (candidates.isEmpty()) return "top_k is empty"
    val seen = HashSet<String>()
    var previous = Float.POSITIVE_INFINITY
    for (candidate in candidates) {
        val candidateKey = candidate.className
        val candidateConfidence = candidate.confidence
        val probability = probs[candidateKey]
            ?: return "top_k contains a class absent from all_probs"
        if (
            !isWellFormedClassKey(candidateKey) ||
            !seen.add(candidateKey) ||
            !isUnitProbability(candidateConfidence)
        ) {
            return "top_k contains an invalid candidate"
        }
        if (candidateConfidence > previous + PROBABILITY_WIRE_TOLERANCE) {
            return "top_k is not probability ordered"
        }
        if (kotlin.math.abs(candidateConfidence - probability) > PROBABILITY_WIRE_TOLERANCE) {
            return "top_k disagrees with all_probs"
        }
        previous = candidateConfidence
    }
    if (candidates.first().className != key) return "top_k does not start with class_name"
    return null
}

// The server rounds confidence to 3 decimals and all_probs/top_k to 4. This
// tolerance permits only that documented wire-format loss, not a different
// probability or class.
private const val PROBABILITY_WIRE_TOLERANCE = 0.0011f

private fun isWellFormedClassKey(key: String): Boolean = key.isNotBlank() && key == key.trim()

private fun isUnitProbability(value: Float): Boolean = value.isFinite() && value in 0f..1f

// Strict wire taxonomy for the currently shipped nine-class head. A taxonomy
// migration must update this set and the UI label/care catalog in the same app
// release; accepting a novel probability key with no presentation semantics
// would recreate the blank/misleading diagnosis failure this boundary prevents.
internal val CANONICAL_ANALYSIS_CLASS_KEYS = setOf(
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot",
    "not_a_plant",
    "nutrient_deficiency",
    "pests",
    "powdery_mildew",
    "viral_disease",
)

/** Per-family availability advertised by GET /api/capabilities. The
 *  scan-screen model picker reads this to decide which tiers to offer:
 *  a family with ``available=false`` (typically frontier when the server
 *  hasn't enabled it) is shown disabled. */
@Serializable
data class CapabilitiesResponse(
    @SerializedName("default") @SerialName("default") val default: String = "gamma",
    @SerializedName("families") @SerialName("families") val families: List<FamilyCapability> = emptyList(),
    @SerializedName("server_version") @SerialName("server_version") val serverVersion: String? = null,
)

@Serializable
data class FamilyCapability(
    @SerializedName("id") @SerialName("id") val id: String = "",
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    // "server" or "on_device".
    @SerializedName("location") @SerialName("location") val location: String = "server",
    @SerializedName("model_family") @SerialName("model_family") val modelFamily: String? = null,
)

/** Describes the server-hosted Aqua on-device model (GET /api/aqua/manifest)
 *  so the app can download it once and re-download only when it changes. */
@Serializable
data class AquaManifestResponse(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("version") @SerialName("version") val version: String? = null,
    @SerializedName("sha256") @SerialName("sha256") val sha256: String? = null,
    @SerializedName("classes_sha256") @SerialName("classes_sha256") val classesSha256: String? = null,
    @SerializedName("bundle_sha256") @SerialName("bundle_sha256") val bundleSha256: String? = null,
    @SerializedName("bundle_schema_version") @SerialName("bundle_schema_version") val bundleSchemaVersion: Int = 0,
    @SerializedName("size_bytes") @SerialName("size_bytes") val sizeBytes: Long = 0,
    // The classes.json sidecar passed through verbatim — persisted next to the
    // downloaded .tflite so AquaClassifier reads the same metadata the server
    // shipped (class order, input size, normalisation).
    @SerializedName("classes")
    @SerialName("classes")
    @Serializable(with = GsonJsonObjectSerializer::class)
    val classes: com.google.gson.JsonObject? = null,
)

/** Species block carried inside AnalysisResponse. */
@Serializable
data class SpeciesBlock(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("source") @SerialName("source") val source: String? = null,
    @SerializedName("best") @SerialName("best") val best: SpeciesMatch? = null,
    @SerializedName("candidates") @SerialName("candidates") val candidates: List<SpeciesMatch> = emptyList(),
)

@Serializable
data class SpeciesMatch(
    @SerializedName("scientific_name") @SerialName("scientific_name") val scientificName: String? = null,
    @SerializedName("common_name") @SerialName("common_name") val commonName: String? = null,
    @SerializedName("score") @SerialName("score") val score: Float = 0f,
) {
    /** Human-readable label preferring the common name. */
    val displayName: String?
        get() = commonName?.takeIf { it.isNotBlank() }
            ?: scientificName?.takeIf { it.isNotBlank() }
}

/**
 * /api/community/nearby response. Each item describes a scan that
 * another user in the same region (or the requesting user, on their
 * own past scans) chose to share. ``thumbPath`` is the filename to
 * pass to /api/community/thumb/<filename> — never a full URL.
 */
@Serializable
data class CommunityScansResponse(
    @SerializedName("items") @SerialName("items") val items: List<CommunityScanItem> = emptyList(),
    @SerializedName("summary") @SerialName("summary") val summary: CommunityRegionSummary? = null,
    @SerializedName("scope") @SerialName("scope") val scope: String? = null,
    @SerializedName("country") @SerialName("country") val country: String? = null,
    @SerializedName("country_name_en") @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerializedName("country_name_ru") @SerialName("country_name_ru") val countryNameRu: String? = null,
    // True on the map endpoint when the bbox held more scans than the cap, so
    // the UI can prompt "zoom in to see all". Null/false on the feed.
    @SerializedName("truncated") @SerialName("truncated") val truncated: Boolean = false,
    // True when the request carried neither coords nor a country, so the feed
    // could not be geo-scoped. Lets the UI prompt "turn on location" instead of
    // showing a misleading "no scans near you" empty state.
    @SerializedName("location_required") @SerialName("location_required")
    val locationRequired: Boolean = false,
    // Legacy timestamp cursor from the pre-`next_cursor` feed. The current
    // server no longer sends it and no client code reads it; kept only so an
    // older deployment's `next_before` still lands somewhere typed.
    @SerializedName("next_before") @SerialName("next_before") val nextBefore: Long? = null,
    // Recommended pages use an opaque composite cursor because their order is
    // score + time + id, not time alone. Clients must return it unchanged.
    @SerializedName("next_cursor") @SerialName("next_cursor") val nextCursor: String? = null,
    @SerializedName("ranking") @SerialName("ranking") val ranking: String? = null,
)

@Serializable
data class CommunityScanItem(
    // Non-null with safe defaults: Gson does not honour Kotlin non-null types,
    // so an item missing scan_id / disease would otherwise inject null and NPE
    // the whole feed render on first access (consumers pass these into non-null
    // String params). Defaulting degrades one bad row to blank instead.
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String = "",
    @SerializedName("country") @SerialName("country") val country: String = "",
    // Localized country name resolved server-side (e.g. "Poland" / "Польша"),
    // so cards render a real place name instead of the raw "PL" ISO code.
    // Null on older responses — callers fall back to [country].
    @SerializedName("country_name_en") @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerializedName("country_name_ru") @SerialName("country_name_ru") val countryNameRu: String? = null,
    // `region` is the subdivision (nearby key); `city` is the finer label —
    // cards prefer city, falling back to region.
    @SerializedName("region") @SerialName("region") val region: String = "",
    @SerializedName("city") @SerialName("city") val city: String = "",
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("confidence") @SerialName("confidence") val confidence: Float? = null,
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("species_score") @SerialName("species_score") val speciesScore: Float? = null,
    @SerializedName("thumb_path") @SerialName("thumb_path") val thumbPath: String? = null,
    @SerializedName("locale") @SerialName("locale") val locale: String? = null,
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0L,
    // How many people opened this shared scan (server-attached on the feed).
    @SerializedName("view_count") @SerialName("view_count") val viewCount: Int = 0,
    // Approximate great-circle distance from the requester, in km, computed
    // server-side (the raw coords never leave the server). Null when the feed
    // had no requester anchor (country/worldwide scope). Shown as "≈ N km near
    // you" on nearby cards.
    @SerializedName("distance_km") @SerialName("distance_km") val distanceKm: Float? = null,
    // ~1 km-rounded coords — populated only by the map endpoint (the feed
    // strips them), so the map can pin a marker. Null elsewhere.
    @SerializedName("lat") @SerialName("lat") val lat: Double? = null,
    @SerializedName("lon") @SerialName("lon") val lon: Double? = null,
)

/** /api/community/heatmap — k-anonymized scan density for the zoomed-out map
 *  layer. Each cell is a center point + a scan count (>= the server's k). */
@Serializable
data class CommunityHeatmapResponse(
    @SerializedName("cells") @SerialName("cells") val cells: List<CommunityHeatCell> = emptyList(),
    @SerializedName("cell_size_deg") @SerialName("cell_size_deg") val cellSizeDeg: Double = 0.0,
    @SerializedName("max_count") @SerialName("max_count") val maxCount: Int = 0,
    @SerializedName("total") @SerialName("total") val total: Int = 0,
    @SerializedName("k") @SerialName("k") val k: Int = 0,
    @SerializedName("days") @SerialName("days") val days: Int = 0,
)

@Serializable
data class CommunityHeatCell(
    @SerializedName("lat") @SerialName("lat") val lat: Double = 0.0,
    @SerializedName("lon") @SerialName("lon") val lon: Double = 0.0,
    @SerializedName("count") @SerialName("count") val count: Int = 0,
)

@Serializable
data class CommunityRegionSummary(
    // Nullable: the server sends an explicit `null` here whenever the request
    // can't be resolved to an ISO country (worldwide scope, or a place with no
    // country code — e.g. Abkhazia). A non-null type made strict kotlinx
    // decoding throw on that null and surfaced as "Couldn't reach the community".
    @SerializedName("country") @SerialName("country") val country: String? = null,
    @SerializedName("country_name_en") @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerializedName("country_name_ru") @SerialName("country_name_ru") val countryNameRu: String? = null,
    @SerializedName("scope") @SerialName("scope") val scope: String? = null,
    @SerializedName("region") @SerialName("region") val region: String? = null,
    @SerializedName("total") @SerialName("total") val total: Int = 0,
    @SerializedName("last_7d") @SerialName("last_7d") val last7d: Int = 0,
    @SerializedName("top_diseases") @SerialName("top_diseases") val topDiseases: List<CommunityTopDisease> = emptyList(),
    @SerializedName("top_regions") @SerialName("top_regions") val topRegions: List<CommunityTopRegion> = emptyList(),
)

@Serializable
data class CommunityTopDisease(
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
)

@Serializable
data class CommunityTopRegion(
    @SerializedName("region") @SerialName("region") val region: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
)

/** /api/community/scan response shape. */
@Serializable
data class CommunityScanUploadResponse(
    @SerializedName("ok") @SerialName("ok") val ok: Boolean = false,
    @SerializedName("id") @SerialName("id") val id: Long = 0,
)

/** /api/telemetry/errors request body. One client-side problem report:
 *  errors-only diagnostic context (no images, no location, no PII). */
@Serializable
data class ErrorReport(
    @SerializedName("telemetry_consent")
    @SerialName("telemetry_consent")
    val telemetryConsent: Boolean,
    @SerializedName("telemetry_consent_version")
    @SerialName("telemetry_consent_version")
    val telemetryConsentVersion: String,
    @SerializedName("stage") @SerialName("stage") val stage: String,
    @SerializedName("kind") @SerialName("kind") val kind: String,
    @SerializedName("fatal") @SerialName("fatal") val fatal: Boolean = false,
    @SerializedName("http_status") @SerialName("http_status") val httpStatus: Int? = null,
    @SerializedName("family") @SerialName("family") val family: String? = null,
    @SerializedName("app_version") @SerialName("app_version") val appVersion: String? = null,
    @SerializedName("android_api") @SerialName("android_api") val androidApi: Int? = null,
    @SerializedName("breadcrumbs") @SerialName("breadcrumbs") val breadcrumbs: List<String>? = null,
)

@Serializable
data class CommunityBlockItem(
    @SerializedName("block_id") @SerialName("block_id") val blockId: String = "",
    @SerializedName("nickname") @SerialName("nickname") val nickname: String? = null,
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0L,
)

@Serializable
data class CommunityBlocksResponse(
    @SerializedName("items") @SerialName("items") val items: List<CommunityBlockItem> = emptyList(),
)

/** /api/community/view request body — records that the caller opened
 *  someone else's shared scan (deduped per viewer server-side). */
@Serializable
data class CommunityViewRequest(
    // Single-open shape. `scanIds` (batch, feed-scroll impressions) is the
    // alternative — the server requires exactly one of the two to be set.
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String? = null,
    @SerializedName("scan_ids") @SerialName("scan_ids") val scanIds: List<String>? = null,
)

/** Guide article about plant care */
data class GuideItem(
    val id: String,
    val titleEn: String,
    val titleRu: String,
    val descriptionEn: String,
    val descriptionRu: String,
    val contentEn: String,
    val contentRu: String,
    val iconRes: Int,
    val category: GuideCategory
)

enum class GuideCategory {
    COMMON_DISEASES,

    // Also hosts lighting content (the standalone Lighting tab was merged
    // in — five tabs read better than six and lighting IS care).
    CARE_TIPS,
    WATERING,
    PESTS,

    // "Using the app" articles (reading results, community, data) — the
    // handbook content that used to live as tip cards on the Profile tab.
    HELP
}

/** Popular species/diseases row entries (shared by /api/popular). */
@Serializable
data class TrendingSpecies(
    @SerializedName("species") @SerialName("species") val species: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
    @SerializedName("percent") @SerialName("percent") val percent: Int = 0,
)

@Serializable
data class TrendingDisease(
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
    @SerializedName("percent") @SerialName("percent") val percent: Int = 0,
)

/** Scan history item for display */
data class ScanHistoryItem(
    val id: Long,
    val imagePath: String,
    val diseaseName: String,
    val diseaseNameRu: String,
    val confidence: Float,
    val isHealthy: Boolean,
    /**
     * True when the scan was classified as `not_a_plant`. Mutually
     * exclusive with `isHealthy`; `isHealthy=false && isNotAPlant=false`
     * means a real disease was diagnosed.
     */
    val isNotAPlant: Boolean,
    val timestamp: Long,
    /** Globally unique community id, empty for legacy/local-only rows. */
    val clientScanId: String = "",
)

/**
 * /api/chat request body. The server expects:
 *
 *  - session_id  : stable client-side id, e.g. "plant_${plantId}".
 *                  Per-session caps (turns, tokens) are tracked
 *                  server-side off this key.
 *  - message     : the user's question (≤ 2000 chars).
 *  - locale      : "en" | "ru" — the server uses this to bias the
 *                  system prompt towards the right language.
 *  - diagnosis   : optional dict shaped like an /api/analyze response
 *                  (or any subset). Used by the server to ground the
 *                  reply in the latest classifier output. Capped to
 *                  8 KB on the wire.
 */
@Serializable
data class ChatRequest(
    @SerializedName("session_id") @SerialName("session_id") val sessionId: String,
    @SerializedName("message") @SerialName("message") val message: String,
    @SerializedName("locale") @SerialName("locale") val locale: String = "en",
    @SerializedName("diagnosis")
    @SerialName("diagnosis")
    @Serializable(with = AnyMapSerializer::class)
    val diagnosis: Map<String, Any?>? = null,
)

/**
 * /api/chat response. The server always returns 200 OK; failure /
 * unavailability is signalled via `available=false` plus a human-
 * readable `reason`. The UI should surface `fallback` (when present)
 * verbatim instead of inventing its own copy.
 */
@Serializable
data class ChatResponse(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("reply") @SerialName("reply") val reply: String = "",
    @SerializedName("reason") @SerialName("reason") val reason: String? = null,
    @SerializedName("fallback") @SerialName("fallback") val fallback: String? = null,
    @SerializedName("model") @SerialName("model") val model: String? = null,
    @SerializedName("provider") @SerialName("provider") val provider: String? = null,
    @SerializedName("usage") @SerialName("usage") val usage: ChatUsage? = null,
    @SerializedName("session") @SerialName("session") val session: ChatSession? = null,
)

@Serializable
data class ChatUsage(
    @SerializedName("input_tokens") @SerialName("input_tokens") val inputTokens: Int? = null,
    @SerializedName("output_tokens") @SerialName("output_tokens") val outputTokens: Int? = null,
)

@Serializable
data class ChatSession(
    @SerializedName("session_id") @SerialName("session_id") val sessionId: String,
    @SerializedName("turns_used") @SerialName("turns_used") val turnsUsed: Int = 0,
    @SerializedName("turns_remaining") @SerialName("turns_remaining") val turnsRemaining: Int = 0,
    @SerializedName("tokens_used") @SerialName("tokens_used") val tokensUsed: Int = 0,
    @SerializedName("tokens_remaining") @SerialName("tokens_remaining") val tokensRemaining: Int = 0,
)

/** PlantNet species candidate from /api/identify. */
@Serializable
data class SpeciesCandidate(
    @SerializedName("scientific_name") @SerialName("scientific_name") val scientificName: String?,
    @SerializedName("common_names") @SerialName("common_names") val commonNames: List<String> = emptyList(),
    @SerializedName("family") @SerialName("family") val family: String? = null,
    @SerializedName("genus") @SerialName("genus") val genus: String? = null,
    @SerializedName("score") @SerialName("score") val score: Float = 0f,
) {
    val displayCommonName: String?
        get() = commonNames.firstOrNull()?.takeIf { it.isNotBlank() }
}

/**
 * /api/identify response. `available=false` is a graceful state when the
 * server has no PlantNet key, the quota is exhausted, or PlantNet errored —
 * the UI should fall back to manual species entry, not show an error toast.
 */
@Serializable
data class IdentifyResponse(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("results") @SerialName("results") val results: List<SpeciesCandidate> = emptyList(),
    @SerializedName("source") @SerialName("source") val source: String? = null,
    @SerializedName("organ") @SerialName("organ") val organ: String? = null,
    @SerializedName("remaining_requests") @SerialName("remaining_requests") val remainingRequests: Int? = null,
    /** True when the server served this response from its in-memory
     *  PlantNet cache (saves a quota hit). Currently not surfaced in
     *  the UI — kept on the wire for future "served from cache"
     *  indicator next to species suggestions. */
    @SerializedName("cached") @SerialName("cached") val cached: Boolean? = null,
) {
    /** The shared disease pipeline rejected this upload before PlantNet was
     *  called. Keep this separate from [available]: both states deliberately
     *  use `available=false`, but only this one should ask for a clearer plant
     *  photo instead of claiming that the species service is unavailable. */
    val wasRejectedAsNotAPlant: Boolean
        get() = source == "skipped_nap"
}

/** /api/species-info response — localized Wikipedia blurb + lead photo. */
@Serializable
data class SpeciesInfoResponse(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("title") @SerialName("title") val title: String? = null,
    @SerializedName("extract") @SerialName("extract") val extract: String? = null,
    @SerializedName("image_url") @SerialName("image_url") val imageUrl: String? = null,
    @SerializedName("images") @SerialName("images") val images: List<String> = emptyList(),
    @SerializedName("page_url") @SerialName("page_url") val pageUrl: String? = null,
    @SerializedName("source") @SerialName("source") val source: String? = null,
    /** True when `extract` was machine-translated into the requested locale
     *  because no Wikipedia article existed in that language. */
    @SerializedName("translated") @SerialName("translated") val translated: Boolean = false,
    /** "species", or "genus" when no species-level article exists and the
     *  text describes the species' genus instead. Absent on older servers. */
    @SerializedName("level") @SerialName("level") val level: String? = null,
    /** Author and licence of the photo when it did not come from the
     *  Wikipedia lead image (Wikimedia Commons / iNaturalist fallbacks). */
    @SerializedName("image_credit") @SerialName("image_credit") val imageCredit: String? = null,
    @SerializedName("image_license") @SerialName("image_license") val imageLicense: String? = null,
)

/**
 * /api/popular response — the personalized popular-plants bundle for the
 * Home carousel, with lead images already resolved server-side so the row
 * paints in one shot (no per-species Wikipedia round-trips on the client).
 */
@Serializable
data class PopularResponse(
    @SerializedName("scope") @SerialName("scope") val scope: String? = null,
    @SerializedName("country") @SerialName("country") val country: String? = null,
    @SerializedName("country_name_en") @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerializedName("country_name_ru") @SerialName("country_name_ru") val countryNameRu: String? = null,
    // Subdivision label when the bundle is scoped to the "nearby" bucket; null
    // for country / placeholder scopes.
    @SerializedName("region") @SerialName("region") val region: String? = null,
    @SerializedName("locale") @SerialName("locale") val locale: String? = null,
    @SerializedName("items") @SerialName("items") val items: List<PopularItem> = emptyList(),
    @SerializedName("top_diseases") @SerialName("top_diseases") val topDiseases: List<TrendingDisease> = emptyList(),
    @SerializedName("total") @SerialName("total") val total: Int = 0,
    // "nearby" | "country" | "placeholder" — drives the title + whether the
    // carousels show "P% of scans" (suppressed for the placeholder).
    @SerializedName("source") @SerialName("source") val source: String? = null,
)

@Serializable
data class PopularItem(
    @SerializedName("scientific") @SerialName("scientific") val scientific: String = "",
    @SerializedName("common_en") @SerialName("common_en") val commonEn: String = "",
    @SerializedName("common_ru") @SerialName("common_ru") val commonRu: String = "",
    @SerializedName("image_url") @SerialName("image_url") val imageUrl: String? = null,
    @SerializedName("count") @SerialName("count") val count: Int = 0,
    @SerializedName("percent") @SerialName("percent") val percent: Int = 0,
)

/**
 * /api/species-diseases response — the conditions a species is commonly
 * affected by indoors (AI, best-effort). `available=false` (no OpenRouter
 * key or any failure) is a graceful state: the species screen then renders
 * the bundled offline disease guide instead, so the section is never empty.
 */
@Serializable
data class SpeciesDiseasesResponse(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("diseases") @SerialName("diseases") val diseases: List<SpeciesDiseaseItem> = emptyList(),
    @SerializedName("source") @SerialName("source") val source: String? = null,
)

/** One common-problem entry. `docKey`, when present, is a disease-guide class
 *  key (see DiseaseCatalog) the client turns into a tappable guide link. */
@Serializable
data class SpeciesDiseaseItem(
    @SerializedName("name") @SerialName("name") val name: String = "",
    @SerializedName("doc_key") @SerialName("doc_key") val docKey: String? = null,
    @SerializedName("note") @SerialName("note") val note: String? = null,
)

/**
 * /api/species-profile response. The endpoint is optional and deliberately
 * returns `available=false` when its AI provider is not configured or a
 * lookup fails; callers hide profile-only sections in that state.
 */
@Serializable
data class SpeciesProfileResponse(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("flowering") @SerialName("flowering") val flowering: SpeciesFlowering? = null,
    @SerializedName("flowering_conditions")
    @SerialName("flowering_conditions")
    val floweringConditions: String? = null,
    @SerializedName("native_range")
    @SerialName("native_range")
    val nativeRange: NativeRange? = null,
    /** Both locales are returned by profile v2 so an offline locale switch
     *  never changes the botanical facts or map geometry. */
    @SerializedName("localized_text")
    @SerialName("localized_text")
    val localizedText: Map<String, SpeciesProfileText>? = null,
    @SerializedName("source") @SerialName("source") val source: String? = null,
)

@Serializable
data class SpeciesProfileText(
    @SerializedName("indoor_note") @SerialName("indoor_note") val indoorNote: String = "",
    @SerializedName("flowering_conditions")
    @SerialName("flowering_conditions")
    val floweringConditions: String = "",
    @SerializedName("native_range_summary")
    @SerialName("native_range_summary")
    val nativeRangeSummary: String = "",
)

@Serializable
data class SpeciesFlowering(
    @SerializedName("monthly_intensity")
    @SerialName("monthly_intensity")
    val monthlyIntensity: List<Int> = emptyList(),
    @SerializedName("indoor_note") @SerialName("indoor_note") val indoorNote: String = "",
)

@Serializable
data class NativeRange(
    @SerializedName("zones") @SerialName("zones") val zones: List<NativeRangeZone> = emptyList(),
    @SerializedName("summary") @SerialName("summary") val summary: String = "",
)

@Serializable
data class NativeRangeZone(
    @SerializedName("country") @SerialName("country") val country: String = "",
    @SerializedName("country_code") @SerialName("country_code") val countryCode: String? = null,
    @SerializedName("status") @SerialName("status") val status: String = "",
)

/** /api/care-plan request body. */
@Serializable
data class CarePlanRequest(
    @SerializedName("disease") @SerialName("disease") val disease: String,
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("light") @SerialName("light") val light: String = "bright_indirect",
    @SerializedName("locale") @SerialName("locale") val locale: String = "en",
    /** Optional season override — `null` lets the server pick from today's date. */
    @SerializedName("season") @SerialName("season") val season: String? = null,
    /** What the plan is for; only [PURPOSE_SCAN] spends the daily care-plan allowance. */
    @SerializedName("purpose") @SerialName("purpose") val purpose: String = PURPOSE_SCAN,
) {
    companion object {
        /** Adapted to a diagnosis: the scan result and the plant it was saved to. */
        const val PURPOSE_SCAN = "scan"

        /** A species' general care on the species page: never metered. */
        const val PURPOSE_SPECIES = "species"

        /** The structured plan behind a garden treatment checklist: no AI summary, never metered. */
        const val PURPOSE_CHECKLIST = "checklist"
    }
}

/** /api/symptom-diagnose request — a map of stable questionId → optionId
 *  (the question/option IDs ship in the app and are scored server-side). */
@Serializable
data class SymptomDiagnoseRequest(
    @SerializedName("answers") @SerialName("answers") val answers: Map<String, String>,
    @SerializedName("locale") @SerialName("locale") val locale: String = "en",
)

/** One ranked result from the symptom checker — a full bilingual care card
 *  (same shape as the server's compact care card) plus its score/confidence. */
@Serializable
data class SymptomResult(
    @SerializedName("class") @SerialName("class") val classKey: String = "",
    @SerializedName("name_en") @SerialName("name_en") val nameEn: String = "",
    @SerializedName("name_ru") @SerialName("name_ru") val nameRu: String = "",
    @SerializedName("description") @SerialName("description") val description: String = "",
    @SerializedName("description_ru") @SerialName("description_ru") val descriptionRu: String = "",
    @SerializedName("treatment") @SerialName("treatment") val treatment: List<String> = emptyList(),
    @SerializedName("treatment_ru") @SerialName("treatment_ru") val treatmentRu: List<String> = emptyList(),
    @SerializedName("prevention") @SerialName("prevention") val prevention: List<String> = emptyList(),
    @SerializedName("prevention_ru") @SerialName("prevention_ru") val preventionRu: List<String> = emptyList(),
    @SerializedName("score") @SerialName("score") val score: Float = 0f,
    @SerializedName("confidence") @SerialName("confidence") val confidence: Float = 0f,
    @SerializedName("rank") @SerialName("rank") val rank: Int = 0,
)

@Serializable
data class SymptomDiagnoseResponse(
    @SerializedName("results") @SerialName("results") val results: List<SymptomResult> = emptyList(),
    @SerializedName("answered") @SerialName("answered") val answered: Int = 0,
    @SerializedName("low_confidence") @SerialName("low_confidence") val lowConfidence: Boolean = false,
)

/**
 * /api/care-plan response. `wateringDaysRaw` is `Any?` because the server
 * may return either an integer interval ("every N days") or the string
 * `"as_needed"` for diseases where a fixed cadence is unsafe. Callers
 * should prefer the `wateringDaysInt` / `wateringDaysIsAsNeeded` helpers.
 */
@Serializable
data class CarePlanResponse(
    @SerializedName("species") @SerialName("species") val species: String = "",
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("light_tier") @SerialName("light_tier") val lightTier: String = "bright_indirect",
    // `watering_days` is int-or-string on the wire (an interval, or the literal
    // "as_needed"). The custom serializer normalizes it for the kotlinx/Ktor
    // path; the field stays Any? so the Gson ResponseCache and the in-app
    // OfflineScanContent.localCarePlan(Int) construction keep working unchanged.
    @SerializedName("watering_days")
    @SerialName("watering_days")
    @Serializable(with = WateringDaysSerializer::class)
    val wateringDaysRaw: Any? = null,
    @SerializedName("fertilise_weeks") @SerialName("fertilise_weeks") val fertiliseWeeks: Int = 0,
    @SerializedName("humidity_target") @SerialName("humidity_target") val humidityTarget: String = "",
    @SerializedName("key_action") @SerialName("key_action") val keyAction: String = "",
    @SerializedName("follow_up_days") @SerialName("follow_up_days") val followUpDays: Int = 14,
    @SerializedName("summary_md") @SerialName("summary_md") val summaryMd: String? = null,
    @SerializedName("toxic_to_pets") @SerialName("toxic_to_pets") val toxicToPets: Boolean = false,
    @SerializedName("toxic_species_list") @SerialName("toxic_species_list") val toxicSpeciesList: List<String> = emptyList(),
    @SerializedName("toxicity_source") @SerialName("toxicity_source") val toxicitySource: String = "unknown",
    /** v2 additive fields — older clients ignore them; new clients
     *  render a richer card with light bands, treatment checklist,
     *  checkpoints and red flags. All are optional so the v1 contract
     *  stays intact for clients on older server versions. */
    @SerializedName("light_lux_target") @SerialName("light_lux_target") val lightLuxTarget: LuxBand? = null,
    @SerializedName("placement") @SerialName("placement") val placement: String? = null,
    @SerializedName("acclimation") @SerialName("acclimation") val acclimation: String? = null,
    @SerializedName("watering_method") @SerialName("watering_method") val wateringMethod: String? = null,
    @SerializedName("soil_mix") @SerialName("soil_mix") val soilMix: String? = null,
    @SerializedName("fertiliser_npk") @SerialName("fertiliser_npk") val fertiliserNpk: String? = null,
    @SerializedName("humidity_band") @SerialName("humidity_band") val humidityBand: HumidityBand? = null,
    @SerializedName("temperature_c") @SerialName("temperature_c") val temperatureC: TemperatureBand? = null,
    @SerializedName("treatment") @SerialName("treatment") val treatment: List<String>? = null,
    @SerializedName("sanitation") @SerialName("sanitation") val sanitation: List<String>? = null,
    @SerializedName("isolation") @SerialName("isolation") val isolation: Boolean = false,
    @SerializedName("red_flags") @SerialName("red_flags") val redFlags: List<String>? = null,
    @SerializedName("checkpoints") @SerialName("checkpoints") val checkpoints: List<CarePlanCheckpoint>? = null,
    @SerializedName("season") @SerialName("season") val season: String? = null,
    @SerializedName("season_note") @SerialName("season_note") val seasonNote: String? = null,
    /** Garden refactor v2 addition — curated, hand-maintained catalogue
     *  entries (never LLM/generated). Absent, not an empty list, when the
     *  disease has no curated products; see server diseases_data.py
     *  TREATMENT_PRODUCTS for why an empty list would be misleading. */
    @SerializedName("products") @SerialName("products") val products: List<CarePlanProduct>? = null,
    @SerializedName("treatment_conditions") @SerialName("treatment_conditions")
    val treatmentConditions: TreatmentConditions? = null,
) {
    val wateringDaysInt: Int?
        get() = when (val v = wateringDaysRaw) {
            is Number -> v.toInt()
            is String -> v.toIntOrNull()
            else -> null
        }

    val wateringDaysIsAsNeeded: Boolean
        get() = (wateringDaysRaw as? String)?.equals("as_needed", ignoreCase = true) == true

    val hasSpecies: Boolean
        get() = species.isNotBlank() && !species.equals("unknown", ignoreCase = true)
}

@Serializable
data class LuxBand(
    @SerializedName("min") @SerialName("min") val min: Int = 0,
    @SerializedName("max") @SerialName("max") val max: Int = 0,
)

@Serializable
data class HumidityBand(
    @SerializedName("min") @SerialName("min") val min: Int = 0,
    @SerializedName("max") @SerialName("max") val max: Int = 0,
)

@Serializable
data class TemperatureBand(
    @SerializedName("day") @SerialName("day") val day: List<Int>? = null,
    @SerializedName("night") @SerialName("night") val night: List<Int>? = null,
)

@Serializable
data class CarePlanCheckpoint(
    @SerializedName("day") @SerialName("day") val day: Int = 0,
    @SerializedName("check") @SerialName("check") val check: String = "",
)

/** One curated treatment-product catalogue entry — see
 *  server/diseases_data.py TREATMENT_PRODUCTS. [activeIngredient] is the
 *  field the UI must always render: brand names differ by market, the
 *  chemistry doesn't. */
@Serializable
data class CarePlanProduct(
    @SerializedName("key") @SerialName("key") val key: String = "",
    @SerializedName("kind") @SerialName("kind") val kind: String = "",
    @SerializedName("name") @SerialName("name") val name: String = "",
    @SerializedName("active_ingredient") @SerialName("active_ingredient")
    val activeIngredient: String = "",
    @SerializedName("note") @SerialName("note") val note: String? = null,
)

/** Application-window guidance shown alongside [CarePlanProduct]s on the
 *  treatment task-detail screen. */
@Serializable
data class TreatmentConditions(
    @SerializedName("best_time") @SerialName("best_time") val bestTime: String = "",
    @SerializedName("temp_c") @SerialName("temp_c") val tempC: List<Int>? = null,
    @SerializedName("dry_hours") @SerialName("dry_hours") val dryHours: Int? = null,
)

/**
 * Reads `watering_days`, which the server sends as either an integer interval
 * or the literal string `"as_needed"`, into the [CarePlanResponse.wateringDaysRaw]
 * `Any?` slot (a number stays a Number, a string stays a String) so the
 * existing `wateringDaysInt` / `wateringDaysIsAsNeeded` helpers — and the Gson
 * ResponseCache round-trip — keep working unchanged. Only the kotlinx/Ktor
 * transport path uses this; Gson uses @SerializedName as before.
 */
@OptIn(ExperimentalSerializationApi::class)
object WateringDaysSerializer : KSerializer<Any?> {
    override val descriptor =
        PrimitiveSerialDescriptor("watering_days", PrimitiveKind.STRING).nullable

    override fun deserialize(decoder: Decoder): Any? {
        val jsonDecoder = decoder as? JsonDecoder ?: return decoder.decodeString()
        return when (val element = jsonDecoder.decodeJsonElement()) {
            is JsonNull -> null
            is JsonPrimitive ->
                if (element.isString) {
                    element.content
                } else {
                    element.intOrNull ?: element.doubleOrNull ?: element.content
                }
            else -> null
        }
    }

    override fun serialize(encoder: Encoder, value: Any?) {
        when (value) {
            null -> encoder.encodeNull()
            is Int -> encoder.encodeInt(value)
            is Number -> encoder.encodeDouble(value.toDouble())
            else -> encoder.encodeString(value.toString())
        }
    }
}

/** Recursively map a loosely-typed [Any] value (String/Number/Boolean/List/Map/
 *  null — the shapes the chat-diagnosis and progression-history payloads use)
 *  to a kotlinx [JsonElement]. */
private fun anyToJsonElement(value: Any?): JsonElement = when (value) {
    null -> JsonNull
    is JsonElement -> value
    is String -> JsonPrimitive(value)
    is Boolean -> JsonPrimitive(value)
    is Number -> JsonPrimitive(value)
    is Map<*, *> -> JsonObject(value.entries.associate { (k, v) -> k.toString() to anyToJsonElement(v) })
    is Iterable<*> -> JsonArray(value.map { anyToJsonElement(it) })
    else -> JsonPrimitive(value.toString())
}

/** Inverse of [anyToJsonElement] — only exercised in tests (these payloads are
 *  request-only and never decoded from the wire), but a [KSerializer] must
 *  implement both directions. */
private fun jsonElementToAny(element: JsonElement): Any? = when (element) {
    is JsonNull -> null
    is JsonObject -> element.mapValues { jsonElementToAny(it.value) }
    is JsonArray -> element.map { jsonElementToAny(it) }
    is JsonPrimitive -> when {
        element.isString -> element.content
        element.booleanOrNull != null -> element.booleanOrNull
        element.longOrNull != null -> element.longOrNull
        element.doubleOrNull != null -> element.doubleOrNull
        else -> element.content
    }
}

/**
 * Serializes a `Map<String, Any?>` (a free-form analyse-shaped payload) as a
 * JSON object on the kotlinx/Ktor path. Keeps the field type `Map<String, Any?>`
 * so the builders (`scanToProgressionPayload`, the chat-diagnosis `mapOf`) and
 * any Gson use stay unchanged. Request-only — `deserialize` is for completeness.
 */
object AnyMapSerializer : KSerializer<Map<String, Any?>> {
    override val descriptor: SerialDescriptor = JsonObject.serializer().descriptor

    override fun serialize(encoder: Encoder, value: Map<String, Any?>) {
        val jsonEncoder = encoder as JsonEncoder
        jsonEncoder.encodeJsonElement(
            JsonObject(value.mapValues { anyToJsonElement(it.value) }),
        )
    }

    override fun deserialize(decoder: Decoder): Map<String, Any?> {
        val jsonObject = (decoder as JsonDecoder).decodeJsonElement().jsonObject
        return jsonObject.mapValues { jsonElementToAny(it.value) }
    }
}

/** `List<Map<String, Any?>>` variant for the progression history payload. */
object AnyMapListSerializer : KSerializer<List<Map<String, Any?>>> {
    private val delegate = ListSerializer(AnyMapSerializer)
    override val descriptor: SerialDescriptor = delegate.descriptor

    override fun serialize(encoder: Encoder, value: List<Map<String, Any?>>) =
        delegate.serialize(encoder, value)

    override fun deserialize(decoder: Decoder): List<Map<String, Any?>> =
        delegate.deserialize(decoder)
}

/**
 * Reads a JSON object straight into a Gson [com.google.gson.JsonObject] on the
 * kotlinx/Ktor path, so the Aqua manifest's `classes` sidecar keeps flowing
 * verbatim into `AquaModelManager.install` (which persists `classes.toString()`)
 * without changing that signature off Gson.
 */
object GsonJsonObjectSerializer : KSerializer<com.google.gson.JsonObject> {
    override val descriptor: SerialDescriptor = JsonObject.serializer().descriptor

    override fun serialize(encoder: Encoder, value: com.google.gson.JsonObject) {
        val jsonEncoder = encoder as JsonEncoder
        jsonEncoder.encodeJsonElement(Json.parseToJsonElement(value.toString()))
    }

    override fun deserialize(decoder: Decoder): com.google.gson.JsonObject {
        val element = (decoder as JsonDecoder).decodeJsonElement()
        return com.google.gson.JsonParser.parseString(element.toString()).asJsonObject
    }
}

/**
 * /api/progression request — time-ordered scan list (oldest first).
 * The server returns the 5-point severity sparkline + a trend slope
 * computed across the full history.
 */
@Serializable
data class ProgressionRequest(
    @SerializedName("history")
    @SerialName("history")
    @Serializable(with = AnyMapListSerializer::class)
    val history: List<Map<String, Any?>>,
)

/**
 * /api/progression response. `trend` ∈ {improving, stable, worsening,
 * baseline}. `baseline` means there were not enough scans to compare
 * against. `sparkline` is always populated (5-point severity series).
 */
@Serializable
data class ProgressionResponse(
    @SerializedName("trend") @SerialName("trend") val trend: String = "baseline",
    @SerializedName("delta_confidence") @SerialName("delta_confidence") val deltaConfidence: Float = 0f,
    @SerializedName("delta_severity") @SerialName("delta_severity") val deltaSeverity: Int = 0,
    @SerializedName("class_changed") @SerialName("class_changed") val classChanged: Boolean = false,
    @SerializedName("previous_class") @SerialName("previous_class") val previousClass: String? = null,
    @SerializedName("current_class") @SerialName("current_class") val currentClass: String? = null,
    @SerializedName("sparkline") @SerialName("sparkline") val sparkline: List<Float>? = null,
    @SerializedName("history_length") @SerialName("history_length") val historyLength: Int? = null,
    @SerializedName("points") @SerialName("points") val points: List<Float>? = null,
    /** Server flag — set when the LAST TWO points both rose; the
     *  plant-detail screen pushes a "take a fresh scan" notification
     *  in that case. */
    @SerializedName("alert") @SerialName("alert") val alert: Boolean = false,
    @SerializedName("alert_reason") @SerialName("alert_reason") val alertReason: String? = null,
)

/**
 * Strict /api/feedback request body. A server-issued one-use receipt locates
 * and updates the matching de-identified training sample.
 *
 *  - `feedbackReceipt`     : opaque, random, short-lived one-use capability.
 *  - `correct`             : thumbs-up / thumbs-down on the diagnosis.
 *  - `correctedClass`      : optional — the user-supplied "real" class
 *                            (must be one of the server's known class
 *                            keys; the server returns 422 otherwise).
 *  - `reason`              : optional — one of `wrong_class`,
 *                            `low_quality_image`, `occluded`,
 *                            `multiple_plants`, `ambiguous_symptoms`,
 *                            `other`.
 * No installation identifier, arbitrary note, file path, or client metadata
 * belongs in this payload.
 */
@Serializable
data class FeedbackRequest(
    @SerializedName("feedback_receipt")
    @SerialName("feedback_receipt")
    val feedbackReceipt: String,
    @SerializedName("correct") @SerialName("correct") val correct: Boolean,
    @SerializedName("corrected_class") @SerialName("corrected_class") val correctedClass: String? = null,
    @SerializedName("reason") @SerialName("reason") val reason: String? = null,
)

/** Response from either structured feedback endpoint. */
@Serializable
data class FeedbackResponse(
    @SerializedName("ok") @SerialName("ok") val ok: Boolean = false,
    @SerializedName("updated") @SerialName("updated") val updated: Boolean = false,
    @SerializedName("stored_image") @SerialName("stored_image") val storedImage: Boolean = false,
)
