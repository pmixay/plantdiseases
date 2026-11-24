package com.plantdiseases.app.data.model

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

// NOTE: kotlinx.serialization is the only JSON library: the Ktor client on the
// wire, and util/AppJson for Room field JSON (ScanEntity's `prediction_set` /
// `regions`) and util/ResponseCache. @SerialName is the JSON name everywhere,
// so rows and cache files written by the earlier Gson-based app keep decoding:
// Gson used the same names through the @SerializedName mirrors removed in 2026-11.

/** Single bounding box returned by the YOLOv8 detector (Stage 1). */
@Serializable
data class DetectionRegion(
    @SerialName("x") val x: Int,
    @SerialName("y") val y: Int,
    @SerialName("width") val width: Int,
    @SerialName("height") val height: Int,
    @SerialName("class") val className: String? = null,
    @SerialName("confidence") val confidence: Float? = null
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
    @SerialName("is_diseased") val isDiseased: Boolean = false,
    @SerialName("detector_confidence") val detectorConfidence: Float = 0f,
    @SerialName("image_width") val imageWidth: Int = 0,
    @SerialName("image_height") val imageHeight: Int = 0,
    @SerialName("regions") val regions: List<DetectionRegion> = emptyList(),
    @SerialName("primary_region") val primaryRegion: DetectionRegion? = null
)

/** Single entry in the server-provided top-k candidate list. */
@Serializable
data class TopKEntry(
    @SerialName("class") val className: String = "",
    @SerialName("confidence") val confidence: Float = 0f,
    @SerialName("name_en") val nameEn: String? = null,
    @SerialName("name_ru") val nameRu: String? = null,
    @SerialName("is_healthy") val isHealthy: Boolean? = null
)

/** One class inside a conformal prediction set, with bilingual labels. */
@Serializable
data class PredictionSetItem(
    @SerialName("class") val className: String = "",
    @SerialName("name_en") val nameEn: String? = null,
    @SerialName("name_ru") val nameRu: String? = null,
)

/**
 * Conformal prediction set — classes the model cannot rule out at the nominal
 * `1-alpha` target. The server currently marks sets advisory; the number is not
 * delivered coverage until a locked independent evaluation exists.
 */
@Serializable
data class PredictionSet(
    @SerialName("classes") val classes: List<String> = emptyList(),
    @SerialName("coverage") val coverage: Float = 0f,
    @SerialName("method") val method: String? = null,
    @SerialName("items") val items: List<PredictionSetItem> = emptyList(),
    /**
     * True until the conformal calibration set is large enough for the
     * coverage figure to carry a real statistical guarantee. When
     * `advisory`, the UI must render the set as "other candidates to
     * consider" rather than claiming "<coverage>% guaranteed coverage".
     */
    @SerialName("advisory") val advisory: Boolean = true,
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
    @SerialName("available") val available: Boolean = true,
    @SerialName("reason") val unavailableReason: String? = null,
    @SerialName("message") val availabilityMessage: String? = null,
    // Probability-backed classifier argmax (e.g. ``leaf_spot``). Rejection
    // gates deliberately preserve this diagnostic; [abstained] says whether
    // it may be presented to the user as an accepted diagnosis. The local
    // result UI uses it for structured labels; the feedback endpoint resolves
    // its own prediction through the opaque, one-use receipt and never trusts
    // an echoed client value.
    @SerialName("class_name") val classKey: String? = null,
    // Missing/partial server responses (and older persisted JSON) must not
    // fail the decode or leave a reference field null. Give every
    // always-expected field a safe default so older persisted JSON can be
    // inspected without crashing. The network repository separately applies
    // [diagnosticContractViolation] and rejects a partial HTTP response before
    // it can be shown or saved as a diagnosis.
    @SerialName("disease_name") val diseaseName: String = "",
    @SerialName("disease_name_ru") val diseaseNameRu: String = "",
    @SerialName("confidence") val confidence: Float = 0f,
    @SerialName("description") val description: String = "",
    @SerialName("description_ru") val descriptionRu: String = "",
    @SerialName("treatment") val treatment: List<String> = emptyList(),
    @SerialName("treatment_ru") val treatmentRu: List<String> = emptyList(),
    @SerialName("prevention") val prevention: List<String> = emptyList(),
    @SerialName("prevention_ru") val preventionRu: List<String> = emptyList(),
    @SerialName("is_healthy") val isHealthy: Boolean = false,
    // Compatibility field. Current bundles have no lesion/severity target, so
    // the server conservatively returns ``low`` rather than deriving extent
    // from leaf-box area or class confidence.
    @SerialName("severity") val severity: String? = null,
    @SerialName("detection") val detection: Detection? = null,
    @SerialName("all_probs") val allProbs: Map<String, Float>? = null,
    @SerialName("top_k") val topK: List<TopKEntry>? = null,
    @SerialName("warnings") val warnings: List<String>? = null,
    @SerialName("abstained") val abstained: Boolean = false,
    @SerialName("abstention_reason") val abstentionReason: String? = null,
    @SerialName("verdict_status") val verdictStatus: String? = null,
    @SerialName("prediction_set") val predictionSet: PredictionSet? = null,
    /**
     * Toxic-to-pets surface. `toxicitySource` is `"db"` when the
     * server matched the species against its ASPCA-derived table,
     * `"unknown"` when we couldn't confirm — UI must NOT falsely
     * reassure on unknown.
     */
    @SerialName("toxic_to_pets") val toxicToPets: Boolean = false,
    @SerialName("toxic_species_list") val toxicSpeciesList: List<String> = emptyList(),
    @SerialName("toxicity_source") val toxicitySource: String = "unknown",
    /** Auto-detected species block from the server-side PlantNet
     *  fan-out. ``null`` when the upstream failed; ``available=false``
     *  when PlantNet isn't configured on this server. */
    @SerialName("species") val species: SpeciesBlock? = null,
    @SerialName("elapsed_ms") val elapsedMs: Float? = null,
    // Server-minted community publication capability. It is deliberately
    // distinct from the feedback receipt below and cannot authorize feedback.
    @SerialName("scan_id") val scanId: String? = null,
    // Short-lived, one-use capability for anonymous structured feedback.
    // It is distinct from the community scan token and cannot authorize any
    // other operation.
    @SerialName("feedback_receipt")
    val feedbackReceipt: String? = null,
    // Same-photo dedup signal from /api/analyse. ``isRepeat=true``
    // means the server replayed a previously-computed response from
    // its SHA-256 cache; the verdict is byte-identical to whatever
    // was returned for the first upload of this exact image bytes.
    // ``cachedAt`` is the epoch seconds of that first upload — the
    // client can render "you saw this one earlier today" without
    // pretending the diagnosis is fresh.
    @SerialName("is_repeat") val isRepeat: Boolean = false,
    @SerialName("cached_at") val cachedAt: Double? = null,
    // Model tier that produced this verdict (aqua | gamma | frontier).
    // Echoed back by the server so the client can confirm which family
    // answered (and detect a server-side downgrade). On-device Aqua
    // results set this locally. Defaults to gamma for older responses.
    @SerialName("family") val family: String = "gamma",
    @SerialName("requested_family") val requestedFamily: String? = null,
    @SerialName("family_fallback_reason") val familyFallbackReason: String? = null,
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
    @SerialName("default") val default: String = "gamma",
    @SerialName("families") val families: List<FamilyCapability> = emptyList(),
    @SerialName("server_version") val serverVersion: String? = null,
)

@Serializable
data class FamilyCapability(
    @SerialName("id") val id: String = "",
    @SerialName("available") val available: Boolean = false,
    // "server" or "on_device".
    @SerialName("location") val location: String = "server",
    @SerialName("model_family") val modelFamily: String? = null,
)

/** Describes the server-hosted Aqua on-device model (GET /api/aqua/manifest)
 *  so the app can download it once and re-download only when it changes. */
@Serializable
data class AquaManifestResponse(
    @SerialName("available") val available: Boolean = false,
    @SerialName("version") val version: String? = null,
    @SerialName("sha256") val sha256: String? = null,
    @SerialName("classes_sha256") val classesSha256: String? = null,
    @SerialName("bundle_sha256") val bundleSha256: String? = null,
    @SerialName("bundle_schema_version") val bundleSchemaVersion: Int = 0,
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    // The classes.json sidecar passed through verbatim — persisted next to the
    // downloaded .tflite so AquaClassifier reads the same metadata the server
    // shipped (class order, input size, normalisation).
    @SerialName("classes")
    val classes: JsonObject? = null,
)

/** Species block carried inside AnalysisResponse. */
@Serializable
data class SpeciesBlock(
    @SerialName("available") val available: Boolean = false,
    @SerialName("source") val source: String? = null,
    @SerialName("best") val best: SpeciesMatch? = null,
    @SerialName("candidates") val candidates: List<SpeciesMatch> = emptyList(),
)

@Serializable
data class SpeciesMatch(
    @SerialName("scientific_name") val scientificName: String? = null,
    @SerialName("common_name") val commonName: String? = null,
    @SerialName("score") val score: Float = 0f,
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
    @SerialName("items") val items: List<CommunityScanItem> = emptyList(),
    @SerialName("summary") val summary: CommunityRegionSummary? = null,
    @SerialName("scope") val scope: String? = null,
    @SerialName("country") val country: String? = null,
    @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerialName("country_name_ru") val countryNameRu: String? = null,
    // True on the map endpoint when the bbox held more scans than the cap, so
    // the UI can prompt "zoom in to see all". Null/false on the feed.
    @SerialName("truncated") val truncated: Boolean = false,
    // True when the request carried neither coords nor a country, so the feed
    // could not be geo-scoped. Lets the UI prompt "turn on location" instead of
    // showing a misleading "no scans near you" empty state.
    @SerialName("location_required")
    val locationRequired: Boolean = false,
    // Legacy timestamp cursor from the pre-`next_cursor` feed. The current
    // server no longer sends it and no client code reads it; kept only so an
    // older deployment's `next_before` still lands somewhere typed.
    @SerialName("next_before") val nextBefore: Long? = null,
    // Recommended pages use an opaque composite cursor because their order is
    // score + time + id, not time alone. Clients must return it unchanged.
    @SerialName("next_cursor") val nextCursor: String? = null,
    @SerialName("ranking") val ranking: String? = null,
)

@Serializable
data class CommunityScanItem(
    // Non-null with safe defaults: an item missing scan_id / disease must still
    // decode, not fail the whole feed render (consumers pass these into non-null
    // String params). Defaulting degrades one bad row to blank instead.
    @SerialName("scan_id") val scanId: String = "",
    @SerialName("country") val country: String = "",
    // Localized country name resolved server-side (e.g. "Poland" / "Польша"),
    // so cards render a real place name instead of the raw "PL" ISO code.
    // Null on older responses — callers fall back to [country].
    @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerialName("country_name_ru") val countryNameRu: String? = null,
    // `region` is the subdivision (nearby key); `city` is the finer label —
    // cards prefer city, falling back to region.
    @SerialName("region") val region: String = "",
    @SerialName("city") val city: String = "",
    @SerialName("disease") val disease: String = "",
    @SerialName("confidence") val confidence: Float? = null,
    @SerialName("species") val species: String? = null,
    @SerialName("species_score") val speciesScore: Float? = null,
    @SerialName("thumb_path") val thumbPath: String? = null,
    @SerialName("locale") val locale: String? = null,
    @SerialName("created_at") val createdAt: Long = 0L,
    // How many people opened this shared scan (server-attached on the feed).
    @SerialName("view_count") val viewCount: Int = 0,
    // Approximate great-circle distance from the requester, in km, computed
    // server-side (the raw coords never leave the server). Null when the feed
    // had no requester anchor (country/worldwide scope). Shown as "≈ N km near
    // you" on nearby cards.
    @SerialName("distance_km") val distanceKm: Float? = null,
    // ~1 km-rounded coords — populated only by the map endpoint (the feed
    // strips them), so the map can pin a marker. Null elsewhere.
    @SerialName("lat") val lat: Double? = null,
    @SerialName("lon") val lon: Double? = null,
)

/** /api/community/heatmap — k-anonymized scan density for the zoomed-out map
 *  layer. Each cell is a center point + a scan count (>= the server's k). */
@Serializable
data class CommunityHeatmapResponse(
    @SerialName("cells") val cells: List<CommunityHeatCell> = emptyList(),
    @SerialName("cell_size_deg") val cellSizeDeg: Double = 0.0,
    @SerialName("max_count") val maxCount: Int = 0,
    @SerialName("total") val total: Int = 0,
    @SerialName("k") val k: Int = 0,
    @SerialName("days") val days: Int = 0,
)

@Serializable
data class CommunityHeatCell(
    @SerialName("lat") val lat: Double = 0.0,
    @SerialName("lon") val lon: Double = 0.0,
    @SerialName("count") val count: Int = 0,
)

@Serializable
data class CommunityRegionSummary(
    // Nullable: the server sends an explicit `null` here whenever the request
    // can't be resolved to an ISO country (worldwide scope, or a place with no
    // country code — e.g. Abkhazia). A non-null type made strict kotlinx
    // decoding throw on that null and surfaced as "Couldn't reach the community".
    @SerialName("country") val country: String? = null,
    @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerialName("country_name_ru") val countryNameRu: String? = null,
    @SerialName("scope") val scope: String? = null,
    @SerialName("region") val region: String? = null,
    @SerialName("total") val total: Int = 0,
    @SerialName("last_7d") val last7d: Int = 0,
    @SerialName("top_diseases") val topDiseases: List<CommunityTopDisease> = emptyList(),
    @SerialName("top_regions") val topRegions: List<CommunityTopRegion> = emptyList(),
)

@Serializable
data class CommunityTopDisease(
    @SerialName("disease") val disease: String = "",
    @SerialName("count") val count: Int = 0,
)

@Serializable
data class CommunityTopRegion(
    @SerialName("region") val region: String = "",
    @SerialName("count") val count: Int = 0,
)

/** /api/community/scan response shape. */
@Serializable
data class CommunityScanUploadResponse(
    @SerialName("ok") val ok: Boolean = false,
    @SerialName("id") val id: Long = 0,
)

/** /api/telemetry/errors request body. One client-side problem report:
 *  errors-only diagnostic context (no images, no location, no PII). */
@Serializable
data class ErrorReport(
    @SerialName("telemetry_consent")
    val telemetryConsent: Boolean,
    @SerialName("telemetry_consent_version")
    val telemetryConsentVersion: String,
    @SerialName("stage") val stage: String,
    @SerialName("kind") val kind: String,
    @SerialName("fatal") val fatal: Boolean = false,
    @SerialName("http_status") val httpStatus: Int? = null,
    @SerialName("family") val family: String? = null,
    @SerialName("app_version") val appVersion: String? = null,
    @SerialName("android_api") val androidApi: Int? = null,
    @SerialName("breadcrumbs") val breadcrumbs: List<String>? = null,
)

@Serializable
data class CommunityBlockItem(
    @SerialName("block_id") val blockId: String = "",
    @SerialName("nickname") val nickname: String? = null,
    @SerialName("created_at") val createdAt: Long = 0L,
)

@Serializable
data class CommunityBlocksResponse(
    @SerialName("items") val items: List<CommunityBlockItem> = emptyList(),
)

/** /api/community/view request body — records that the caller opened
 *  someone else's shared scan (deduped per viewer server-side). */
@Serializable
data class CommunityViewRequest(
    // Single-open shape. `scanIds` (batch, feed-scroll impressions) is the
    // alternative — the server requires exactly one of the two to be set.
    @SerialName("scan_id") val scanId: String? = null,
    @SerialName("scan_ids") val scanIds: List<String>? = null,
)

/** Guide article about plant care */
/** One guide article. The text is string resources, so it follows the app's
 *  language like any other UI text. */
data class GuideItem(
    val id: String,
    @androidx.annotation.StringRes val title: Int,
    @androidx.annotation.StringRes val description: Int,
    @androidx.annotation.StringRes val content: Int,
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
    @SerialName("species") val species: String = "",
    @SerialName("count") val count: Int = 0,
    @SerialName("percent") val percent: Int = 0,
)

@Serializable
data class TrendingDisease(
    @SerialName("disease") val disease: String = "",
    @SerialName("count") val count: Int = 0,
    @SerialName("percent") val percent: Int = 0,
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
    @SerialName("session_id") val sessionId: String,
    @SerialName("message") val message: String,
    @SerialName("locale") val locale: String = "en",
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
    @SerialName("available") val available: Boolean = false,
    @SerialName("reply") val reply: String = "",
    @SerialName("reason") val reason: String? = null,
    @SerialName("fallback") val fallback: String? = null,
    @SerialName("model") val model: String? = null,
    @SerialName("provider") val provider: String? = null,
    @SerialName("usage") val usage: ChatUsage? = null,
    @SerialName("session") val session: ChatSession? = null,
)

@Serializable
data class ChatUsage(
    @SerialName("input_tokens") val inputTokens: Int? = null,
    @SerialName("output_tokens") val outputTokens: Int? = null,
)

@Serializable
data class ChatSession(
    @SerialName("session_id") val sessionId: String,
    @SerialName("turns_used") val turnsUsed: Int = 0,
    @SerialName("turns_remaining") val turnsRemaining: Int = 0,
    @SerialName("tokens_used") val tokensUsed: Int = 0,
    @SerialName("tokens_remaining") val tokensRemaining: Int = 0,
)

/** PlantNet species candidate from /api/identify. */
@Serializable
data class SpeciesCandidate(
    @SerialName("scientific_name") val scientificName: String?,
    @SerialName("common_names") val commonNames: List<String> = emptyList(),
    @SerialName("family") val family: String? = null,
    @SerialName("genus") val genus: String? = null,
    @SerialName("score") val score: Float = 0f,
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
    @SerialName("available") val available: Boolean = false,
    @SerialName("results") val results: List<SpeciesCandidate> = emptyList(),
    @SerialName("source") val source: String? = null,
    @SerialName("organ") val organ: String? = null,
    @SerialName("remaining_requests") val remainingRequests: Int? = null,
    /** True when the server served this response from its in-memory
     *  PlantNet cache (saves a quota hit). Currently not surfaced in
     *  the UI — kept on the wire for future "served from cache"
     *  indicator next to species suggestions. */
    @SerialName("cached") val cached: Boolean? = null,
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
    @SerialName("available") val available: Boolean = false,
    @SerialName("title") val title: String? = null,
    @SerialName("extract") val extract: String? = null,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("images") val images: List<String> = emptyList(),
    @SerialName("page_url") val pageUrl: String? = null,
    @SerialName("source") val source: String? = null,
    /** True when `extract` was machine-translated into the requested locale
     *  because no Wikipedia article existed in that language. */
    @SerialName("translated") val translated: Boolean = false,
    /** "species", or "genus" when no species-level article exists and the
     *  text describes the species' genus instead. Absent on older servers. */
    @SerialName("level") val level: String? = null,
    /** Author and licence of the photo when it did not come from the
     *  Wikipedia lead image (Wikimedia Commons / iNaturalist fallbacks). */
    @SerialName("image_credit") val imageCredit: String? = null,
    @SerialName("image_license") val imageLicense: String? = null,
)

/**
 * /api/popular response — the personalized popular-plants bundle for the
 * Home carousel, with lead images already resolved server-side so the row
 * paints in one shot (no per-species Wikipedia round-trips on the client).
 */
@Serializable
data class PopularResponse(
    @SerialName("scope") val scope: String? = null,
    @SerialName("country") val country: String? = null,
    @SerialName("country_name_en") val countryNameEn: String? = null,
    @SerialName("country_name_ru") val countryNameRu: String? = null,
    // Subdivision label when the bundle is scoped to the "nearby" bucket; null
    // for country / placeholder scopes.
    @SerialName("region") val region: String? = null,
    @SerialName("locale") val locale: String? = null,
    @SerialName("items") val items: List<PopularItem> = emptyList(),
    @SerialName("top_diseases") val topDiseases: List<TrendingDisease> = emptyList(),
    @SerialName("total") val total: Int = 0,
    // "nearby" | "country" | "placeholder" — drives the title + whether the
    // carousels show "P% of scans" (suppressed for the placeholder).
    @SerialName("source") val source: String? = null,
)

@Serializable
data class PopularItem(
    @SerialName("scientific") val scientific: String = "",
    @SerialName("common_en") val commonEn: String = "",
    @SerialName("common_ru") val commonRu: String = "",
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("count") val count: Int = 0,
    @SerialName("percent") val percent: Int = 0,
)

/**
 * /api/species-diseases response — the conditions a species is commonly
 * affected by indoors (AI, best-effort). `available=false` (no OpenRouter
 * key or any failure) is a graceful state: the species screen then renders
 * the bundled offline disease guide instead, so the section is never empty.
 */
@Serializable
data class SpeciesDiseasesResponse(
    @SerialName("available") val available: Boolean = false,
    @SerialName("species") val species: String? = null,
    @SerialName("diseases") val diseases: List<SpeciesDiseaseItem> = emptyList(),
    @SerialName("source") val source: String? = null,
)

/** One common-problem entry. `docKey`, when present, is a disease-guide class
 *  key (see DiseaseCatalog) the client turns into a tappable guide link. */
@Serializable
data class SpeciesDiseaseItem(
    @SerialName("name") val name: String = "",
    @SerialName("doc_key") val docKey: String? = null,
    @SerialName("note") val note: String? = null,
)

/**
 * /api/species-profile response. The endpoint is optional and deliberately
 * returns `available=false` when its AI provider is not configured or a
 * lookup fails; callers hide profile-only sections in that state.
 */
@Serializable
data class SpeciesProfileResponse(
    @SerialName("available") val available: Boolean = false,
    @SerialName("species") val species: String? = null,
    @SerialName("flowering") val flowering: SpeciesFlowering? = null,
    @SerialName("flowering_conditions")
    val floweringConditions: String? = null,
    @SerialName("native_range")
    val nativeRange: NativeRange? = null,
    /** Both locales are returned by profile v2 so an offline locale switch
     *  never changes the botanical facts or map geometry. */
    @SerialName("localized_text")
    val localizedText: Map<String, SpeciesProfileText>? = null,
    @SerialName("source") val source: String? = null,
)

@Serializable
data class SpeciesProfileText(
    @SerialName("indoor_note") val indoorNote: String = "",
    @SerialName("flowering_conditions")
    val floweringConditions: String = "",
    @SerialName("native_range_summary")
    val nativeRangeSummary: String = "",
)

@Serializable
data class SpeciesFlowering(
    @SerialName("monthly_intensity")
    val monthlyIntensity: List<Int> = emptyList(),
    @SerialName("indoor_note") val indoorNote: String = "",
)

@Serializable
data class NativeRange(
    @SerialName("zones") val zones: List<NativeRangeZone> = emptyList(),
    @SerialName("summary") val summary: String = "",
)

@Serializable
data class NativeRangeZone(
    @SerialName("country") val country: String = "",
    @SerialName("country_code") val countryCode: String? = null,
    @SerialName("status") val status: String = "",
)

/** /api/care-plan request body. */
@Serializable
data class CarePlanRequest(
    @SerialName("disease") val disease: String,
    @SerialName("species") val species: String? = null,
    @SerialName("light") val light: String = "bright_indirect",
    @SerialName("locale") val locale: String = "en",
    /** Optional season override — `null` lets the server pick from today's date. */
    @SerialName("season") val season: String? = null,
    /** What the plan is for; only [PURPOSE_SCAN] spends the daily care-plan allowance. */
    @SerialName("purpose") val purpose: String = PURPOSE_SCAN,
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
    @SerialName("answers") val answers: Map<String, String>,
    @SerialName("locale") val locale: String = "en",
)

/** One ranked result from the symptom checker — a full bilingual care card
 *  (same shape as the server's compact care card) plus its score/confidence. */
@Serializable
data class SymptomResult(
    @SerialName("class") val classKey: String = "",
    @SerialName("name_en") val nameEn: String = "",
    @SerialName("name_ru") val nameRu: String = "",
    @SerialName("description") val description: String = "",
    @SerialName("description_ru") val descriptionRu: String = "",
    @SerialName("treatment") val treatment: List<String> = emptyList(),
    @SerialName("treatment_ru") val treatmentRu: List<String> = emptyList(),
    @SerialName("prevention") val prevention: List<String> = emptyList(),
    @SerialName("prevention_ru") val preventionRu: List<String> = emptyList(),
    @SerialName("score") val score: Float = 0f,
    @SerialName("confidence") val confidence: Float = 0f,
    @SerialName("rank") val rank: Int = 0,
)

@Serializable
data class SymptomDiagnoseResponse(
    @SerialName("results") val results: List<SymptomResult> = emptyList(),
    @SerialName("answered") val answered: Int = 0,
    @SerialName("low_confidence") val lowConfidence: Boolean = false,
)

/**
 * /api/care-plan response. `wateringDaysRaw` is `Any?` because the server
 * may return either an integer interval ("every N days") or the string
 * `"as_needed"` for diseases where a fixed cadence is unsafe. Callers
 * should prefer the `wateringDaysInt` / `wateringDaysIsAsNeeded` helpers.
 */
@Serializable
data class CarePlanResponse(
    @SerialName("species") val species: String = "",
    @SerialName("disease") val disease: String = "",
    @SerialName("light_tier") val lightTier: String = "bright_indirect",
    // `watering_days` is int-or-string on the wire (an interval, or the literal
    // "as_needed"). The custom serializer normalizes it for the kotlinx/Ktor
    // path; the field stays Any? so the in-app
    // OfflineScanContent.localCarePlan(Int) construction keeps working unchanged.
    @SerialName("watering_days")
    @Serializable(with = WateringDaysSerializer::class)
    val wateringDaysRaw: Any? = null,
    @SerialName("fertilise_weeks") val fertiliseWeeks: Int = 0,
    @SerialName("humidity_target") val humidityTarget: String = "",
    @SerialName("key_action") val keyAction: String = "",
    @SerialName("follow_up_days") val followUpDays: Int = 14,
    @SerialName("summary_md") val summaryMd: String? = null,
    @SerialName("toxic_to_pets") val toxicToPets: Boolean = false,
    @SerialName("toxic_species_list") val toxicSpeciesList: List<String> = emptyList(),
    @SerialName("toxicity_source") val toxicitySource: String = "unknown",
    /** v2 additive fields — older clients ignore them; new clients
     *  render a richer card with light bands, treatment checklist,
     *  checkpoints and red flags. All are optional so the v1 contract
     *  stays intact for clients on older server versions. */
    @SerialName("light_lux_target") val lightLuxTarget: LuxBand? = null,
    @SerialName("placement") val placement: String? = null,
    @SerialName("acclimation") val acclimation: String? = null,
    @SerialName("watering_method") val wateringMethod: String? = null,
    @SerialName("soil_mix") val soilMix: String? = null,
    @SerialName("fertiliser_npk") val fertiliserNpk: String? = null,
    @SerialName("humidity_band") val humidityBand: HumidityBand? = null,
    @SerialName("temperature_c") val temperatureC: TemperatureBand? = null,
    @SerialName("treatment") val treatment: List<String>? = null,
    @SerialName("sanitation") val sanitation: List<String>? = null,
    @SerialName("isolation") val isolation: Boolean = false,
    @SerialName("red_flags") val redFlags: List<String>? = null,
    @SerialName("checkpoints") val checkpoints: List<CarePlanCheckpoint>? = null,
    @SerialName("season") val season: String? = null,
    @SerialName("season_note") val seasonNote: String? = null,
    /** Garden refactor v2 addition — curated, hand-maintained catalogue
     *  entries (never LLM/generated). Absent, not an empty list, when the
     *  disease has no curated products; see server diseases_data.py
     *  TREATMENT_PRODUCTS for why an empty list would be misleading. */
    @SerialName("products") val products: List<CarePlanProduct>? = null,
    @SerialName("treatment_conditions")
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
    @SerialName("min") val min: Int = 0,
    @SerialName("max") val max: Int = 0,
)

@Serializable
data class HumidityBand(
    @SerialName("min") val min: Int = 0,
    @SerialName("max") val max: Int = 0,
)

@Serializable
data class TemperatureBand(
    @SerialName("day") val day: List<Int>? = null,
    @SerialName("night") val night: List<Int>? = null,
)

@Serializable
data class CarePlanCheckpoint(
    @SerialName("day") val day: Int = 0,
    @SerialName("check") val check: String = "",
)

/** One curated treatment-product catalogue entry — see
 *  server/diseases_data.py TREATMENT_PRODUCTS. [activeIngredient] is the
 *  field the UI must always render: brand names differ by market, the
 *  chemistry doesn't. */
@Serializable
data class CarePlanProduct(
    @SerialName("key") val key: String = "",
    @SerialName("kind") val kind: String = "",
    @SerialName("name") val name: String = "",
    @SerialName("active_ingredient")
    val activeIngredient: String = "",
    @SerialName("note") val note: String? = null,
)

/** Application-window guidance shown alongside [CarePlanProduct]s on the
 *  treatment task-detail screen. */
@Serializable
data class TreatmentConditions(
    @SerialName("best_time") val bestTime: String = "",
    @SerialName("temp_c") val tempC: List<Int>? = null,
    @SerialName("dry_hours") val dryHours: Int? = null,
)

/**
 * Reads `watering_days`, which the server sends as either an integer interval
 * or the literal string `"as_needed"`, into the [CarePlanResponse.wateringDaysRaw]
 * `Any?` slot (a number stays a Number, a string stays a String) so the
 * existing `wateringDaysInt` / `wateringDaysIsAsNeeded` helpers keep working, on the wire
 * and through the response cache.
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
 * so the builders (`scanToProgressionPayload`, the chat-diagnosis `mapOf`) stay
 * unchanged. Request-only — `deserialize` is for completeness.
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
 * /api/progression request — time-ordered scan list (oldest first).
 * The server returns the 5-point severity sparkline + a trend slope
 * computed across the full history.
 */
@Serializable
data class ProgressionRequest(
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
    @SerialName("trend") val trend: String = "baseline",
    @SerialName("delta_confidence") val deltaConfidence: Float = 0f,
    @SerialName("delta_severity") val deltaSeverity: Int = 0,
    @SerialName("class_changed") val classChanged: Boolean = false,
    @SerialName("previous_class") val previousClass: String? = null,
    @SerialName("current_class") val currentClass: String? = null,
    @SerialName("sparkline") val sparkline: List<Float>? = null,
    @SerialName("history_length") val historyLength: Int? = null,
    @SerialName("points") val points: List<Float>? = null,
    /** Server flag — set when the LAST TWO points both rose; the
     *  plant-detail screen pushes a "take a fresh scan" notification
     *  in that case. */
    @SerialName("alert") val alert: Boolean = false,
    @SerialName("alert_reason") val alertReason: String? = null,
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
    @SerialName("feedback_receipt")
    val feedbackReceipt: String,
    @SerialName("correct") val correct: Boolean,
    @SerialName("corrected_class") val correctedClass: String? = null,
    @SerialName("reason") val reason: String? = null,
)

/** Response from either structured feedback endpoint. */
@Serializable
data class FeedbackResponse(
    @SerialName("ok") val ok: Boolean = false,
    @SerialName("updated") val updated: Boolean = false,
    @SerialName("stored_image") val storedImage: Boolean = false,
)
