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

// NOTE (Ktor + kotlinx.serialization migration, step 2): the response models
// reachable from /api/analyze are dual-annotated — @SerializedName keeps Gson
// working for the endpoints still on Retrofit and for local persistence
// (ScanRepository's Gson cache, ResultActivity intent payloads), while
// @Serializable + @SerialName let the same classes deserialize through the new
// Ktor client. The two annotations mirror each other 1:1.

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
 * A full bilingual care card for one class of a *dual-care* prediction set.
 *
 * Emitted only when the conformal set can't separate the two
 * physiological-stress classes — under-watering (`leaf_scorch_underwatering`)
 * vs `nutrient_deficiency` — whose advice genuinely diverges ("water me" vs
 * "feed me"). The result screen can then show both cards instead of forcing
 * the user to guess which one to act on. Empty for every other set.
 */
@Serializable
data class PredictionSetCareCard(
    @SerializedName("class") @SerialName("class") val className: String = "",
    @SerializedName("name_en") @SerialName("name_en") val nameEn: String? = null,
    @SerializedName("name_ru") @SerialName("name_ru") val nameRu: String? = null,
    @SerializedName("description") @SerialName("description") val description: String = "",
    @SerializedName("description_ru") @SerialName("description_ru") val descriptionRu: String = "",
    @SerializedName("treatment") @SerialName("treatment") val treatment: List<String> = emptyList(),
    @SerializedName("treatment_ru") @SerialName("treatment_ru") val treatmentRu: List<String> = emptyList(),
    @SerializedName("prevention") @SerialName("prevention") val prevention: List<String> = emptyList(),
    @SerializedName("prevention_ru") @SerialName("prevention_ru") val preventionRu: List<String> = emptyList(),
)

/**
 * Conformal prediction set — the classes the model can't rule out at
 * the configured coverage level (e.g. 90 %). Surfaced honestly when
 * calibration data is loaded server-side; the field is null otherwise.
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
    /**
     * True when the set is the ambiguous physiological-stress pair
     * (under-watering vs nutrient deficiency) and `careCards` carries both
     * full care cards so the UI can show "water me" AND "feed me" side by
     * side instead of only the top-1's advice.
     */
    @SerializedName("dual_care") @SerialName("dual_care") val dualCare: Boolean = false,
    @SerializedName("care_cards") @SerialName("care_cards") val careCards: List<PredictionSetCareCard> = emptyList(),
)

/** Response from the server after analyzing an image */
@Serializable
data class AnalysisResponse(
    // Canonical class key (e.g. ``leaf_spot``) the pipeline
    // landed on AFTER post-processing — this is what
    // ``diseaseName`` is derived from. Echo this back as
    // ``predictedClass`` on feedback. ``argmax(allProbs)`` may
    // disagree because the weak-healthy reroute rewrites
    // ``className`` / ``diseaseName`` but leaves ``allProbs``
    // un-mutated; using argmax would poison server-side per-class
    // accuracy metrics.
    @SerializedName("class_name") @SerialName("class_name") val classKey: String? = null,
    // Gson does not honour Kotlin non-null types, so missing/partial
    // server responses would otherwise inject 0f / null silently (and
    // NPE on first access for reference types). Give every
    // always-expected field a safe default so an older / partial
    // response degrades gracefully instead of crashing or showing 0%.
    // (kotlinx.serialization likewise falls back to the default for a
    // missing key, so the two paths degrade identically.)
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
    // Server-computed severity (low|medium|high). Authoritative — the
    // client must persist and forward this rather than re-deriving it
    // from a confidence heuristic (which can disagree with what the
    // pipeline actually scored).
    @SerializedName("severity") @SerialName("severity") val severity: String? = null,
    @SerializedName("detection") @SerialName("detection") val detection: Detection? = null,
    @SerializedName("all_probs") @SerialName("all_probs") val allProbs: Map<String, Float>? = null,
    @SerializedName("top_k") @SerialName("top_k") val topK: List<TopKEntry>? = null,
    @SerializedName("warnings") @SerialName("warnings") val warnings: List<String>? = null,
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
    // Server-minted scan_id from /api/analyse. We MUST echo this on
    // /api/feedback — the server rejects any scan_id it didn't issue
    // (422 "Unknown scan_id"). Optional in the type so older cached
    // responses without it still deserialise cleanly.
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String? = null,
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

/** Opinion counts on the user's OWN shared scans (GET /api/community/
 *  my-scan-opinions/{id}). Polled by OpinionCheckWorker to notify the user
 *  when someone leaves a second opinion. */
data class MyScanOpinionsResponse(
    @SerializedName("scans") val scans: List<ScanOpinionItem> = emptyList(),
    @SerializedName("total_opinions") val totalOpinions: Int = 0,
    @SerializedName("total_views") val totalViews: Int = 0,
)

data class ScanOpinionItem(
    @SerializedName("scan_id") val scanId: String = "",
    @SerializedName("disease") val disease: String = "",
    @SerializedName("opinion_count") val opinionCount: Int = 0,
    @SerializedName("latest_at") val latestAt: Long = 0,
    @SerializedName("view_count") val viewCount: Int = 0,
)

/** Describes the server-hosted Aqua on-device model (GET /api/aqua/manifest)
 *  so the app can download it once and re-download only when it changes. */
data class AquaManifestResponse(
    @SerializedName("available") val available: Boolean = false,
    @SerializedName("version") val version: String? = null,
    @SerializedName("sha256") val sha256: String? = null,
    @SerializedName("size_bytes") val sizeBytes: Long = 0,
    // The classes.json sidecar passed through verbatim — persisted next to the
    // downloaded .tflite so AquaClassifier reads the same metadata the server
    // shipped (class order, input size, normalisation).
    @SerializedName("classes") val classes: com.google.gson.JsonObject? = null,
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
data class CommunityScansResponse(
    @SerializedName("items") val items: List<CommunityScanItem> = emptyList(),
    @SerializedName("summary") val summary: CommunityRegionSummary? = null,
)

data class CommunityScanItem(
    // Non-null with safe defaults: Gson does not honour Kotlin non-null types,
    // so an item missing scan_id / disease would otherwise inject null and NPE
    // the whole feed render on first access (consumers pass these into non-null
    // String params). Defaulting degrades one bad row to blank instead.
    @SerializedName("scan_id") val scanId: String = "",
    @SerializedName("country") val country: String = "",
    @SerializedName("region") val region: String = "",
    @SerializedName("disease") val disease: String = "",
    @SerializedName("confidence") val confidence: Float? = null,
    @SerializedName("species") val species: String? = null,
    @SerializedName("species_score") val speciesScore: Float? = null,
    @SerializedName("thumb_path") val thumbPath: String? = null,
    @SerializedName("locale") val locale: String? = null,
    @SerializedName("created_at") val createdAt: Long = 0L,
    @SerializedName("opinion_count") val opinionCount: Int = 0,
    // The scanner flagged this scan as wanting a community second opinion
    // (they were unsure and are waiting for others to weigh in).
    @SerializedName("wants_opinion") val wantsOpinion: Boolean = false,
    // Stable, anonymous per-author token (a sha256 prefix of the pseudo id —
    // never the id itself). Same author ⇒ same token ⇒ same generated avatar.
    @SerializedName("author_token") val authorToken: String? = null,
)

data class CommunityRegionSummary(
    @SerializedName("country") val country: String = "",
    @SerializedName("region") val region: String? = null,
    @SerializedName("total") val total: Int = 0,
    @SerializedName("last_7d") val last7d: Int = 0,
    @SerializedName("top_diseases") val topDiseases: List<CommunityTopDisease> = emptyList(),
    @SerializedName("top_regions") val topRegions: List<CommunityTopRegion> = emptyList(),
)

data class CommunityTopDisease(
    @SerializedName("disease") val disease: String = "",
    @SerializedName("count") val count: Int = 0,
)

data class CommunityTopRegion(
    @SerializedName("region") val region: String = "",
    @SerializedName("count") val count: Int = 0,
)

/** /api/community/scan response shape. */
@Serializable
data class CommunityScanUploadResponse(
    @SerializedName("ok") @SerialName("ok") val ok: Boolean = false,
    @SerializedName("id") @SerialName("id") val id: Long = 0,
)

/** /api/community/opinion request body. */
data class CommunityOpinionRequest(
    @SerializedName("scan_id") val scanId: String,
    @SerializedName("user_pseudo_id") val userPseudoId: String,
    @SerializedName("suggested_disease") val suggestedDisease: String,
    @SerializedName("note") val note: String? = null,
)

/** /api/community/request-opinion request body. The scanner flags their
 *  OWN scan as wanting a second opinion — they don't suggest a diagnosis. */
data class CommunityRequestOpinionRequest(
    @SerializedName("scan_id") val scanId: String,
    @SerializedName("user_pseudo_id") val userPseudoId: String,
)

/** /api/community/request-opinion response. `flagged` is false when no
 *  owned community row exists yet (upload pending / sharing opted out). */
data class CommunityRequestOpinionResponse(
    @SerializedName("ok") val ok: Boolean = false,
    @SerializedName("flagged") val flagged: Boolean = false,
)

/** One community 'second opinion' on a shared scan. `scanId` is only
 *  populated by the /my-opinions endpoint (which scan the opinion was on). */
data class CommunityOpinion(
    @SerializedName("suggested_disease") val suggestedDisease: String = "",
    @SerializedName("note") val note: String = "",
    @SerializedName("created_at") val createdAt: Long = 0L,
    @SerializedName("scan_id") val scanId: String = "",
)

/** /api/community/opinions/{scan_id} response. POST /opinion returns the
 *  same shape plus `ok` + `count`. */
data class CommunityOpinionsResponse(
    @SerializedName("scan_id") val scanId: String = "",
    @SerializedName("opinions") val opinions: List<CommunityOpinion> = emptyList(),
    @SerializedName("consensus") val consensus: List<CommunityTopDisease> = emptyList(),
    // Distinct-viewer count on this scan (per-user deduped server-side).
    @SerializedName("views") val views: Int = 0,
    @SerializedName("ok") val ok: Boolean = false,
    @SerializedName("count") val count: Int = 0,
)

/** /api/push/register request body — registers this install's FCM token
 *  under its pseudo-id so the server can push opinion notifications. */
data class PushRegisterRequest(
    @SerializedName("user_pseudo_id") val userPseudoId: String,
    @SerializedName("token") val token: String,
    @SerializedName("platform") val platform: String = "android",
)

/** /api/community/view request body — records that the caller opened
 *  someone else's shared scan (deduped per viewer server-side). */
data class CommunityViewRequest(
    @SerializedName("scan_id") val scanId: String,
    @SerializedName("user_pseudo_id") val userPseudoId: String,
)

/** /api/community/scan-counts request + response: batched {opinions, views}
 *  per scan so the Gallery can badge the caller's own rows in one call. */
data class CommunityScanCountsRequest(
    @SerializedName("scan_ids") val scanIds: List<String>,
)

data class CommunityScanCount(
    @SerializedName("opinions") val opinions: Int = 0,
    @SerializedName("views") val views: Int = 0,
)

data class CommunityScanCountsResponse(
    @SerializedName("counts") val counts: Map<String, CommunityScanCount> = emptyMap(),
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
    CARE_TIPS,
    WATERING,
    LIGHTING,
    PESTS
}

/** /api/community/trending — the Home screen's "trending nearby" module.
 *  `scope` reports how far the server had to widen (region/country/global)
 *  so the UI can label the card honestly. */
@Serializable
data class TrendingResponse(
    @SerializedName("scope") @SerialName("scope") val scope: String = "global",
    @SerializedName("country") @SerialName("country") val country: String? = null,
    @SerializedName("region") @SerialName("region") val region: String? = null,
    @SerializedName("window_days") @SerialName("window_days") val windowDays: Int = 7,
    @SerializedName("total") @SerialName("total") val total: Int = 0,
    @SerializedName("top_species") @SerialName("top_species") val topSpecies: List<TrendingSpecies> = emptyList(),
    @SerializedName("top_diseases") @SerialName("top_diseases") val topDiseases: List<TrendingDisease> = emptyList(),
)

@Serializable
data class TrendingSpecies(
    @SerializedName("species") @SerialName("species") val species: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
)

@Serializable
data class TrendingDisease(
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
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
    /** Globally-unique community scan id (empty for legacy rows). Used to
     *  fetch community activity counts for the Gallery badge. */
    val clientScanId: String = "",
    /** Community activity on this scan, filled in lazily by the Gallery after
     *  a batched /community/scan-counts call (0 when none / not shared). */
    val communityOpinions: Int = 0,
    val communityViews: Int = 0,
    /** Opinions on this scan the user hasn't viewed yet (current minus the
     *  per-scan viewed baseline). Drives the red "+N" badge + row highlight. */
    val newOpinions: Int = 0,
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
)

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

/** /api/care-plan request body. */
@Serializable
data class CarePlanRequest(
    @SerializedName("disease") @SerialName("disease") val disease: String,
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("light") @SerialName("light") val light: String = "bright_indirect",
    @SerializedName("locale") @SerialName("locale") val locale: String = "en",
    /** Optional season override — `null` lets the server pick from today's date. */
    @SerializedName("season") @SerialName("season") val season: String? = null,
)

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
                if (element.isString) element.content
                else element.intOrNull ?: element.doubleOrNull ?: element.content
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

/** Server health check response */
@Serializable
data class HealthResponse(
    @SerializedName("status") @SerialName("status") val status: String = "",
    @SerializedName("version") @SerialName("version") val version: String = "",
    @SerializedName("num_classes") @SerialName("num_classes") val numClasses: Int = 0,
    @SerializedName("uptime_seconds") @SerialName("uptime_seconds") val uptimeSeconds: Double? = null,
    @SerializedName("total_requests") @SerialName("total_requests") val totalRequests: Int? = null
)

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
 * /api/feedback request body. The server appends every row — there is
 * no update / delete path.
 *
 *  - `scanId`              : opaque, random per-scan UUID (the
 *                            `client_scan_id` column on the local
 *                            ScanEntity). Never the sequential DB id.
 *  - `correct`             : thumbs-up / thumbs-down on the diagnosis.
 *  - `correctedClass`      : optional — the user-supplied "real" class
 *                            (must be one of the server's known class
 *                            keys; the server returns 422 otherwise).
 *  - `reason`              : optional — one of `wrong_class`,
 *                            `low_quality_image`, `occluded`,
 *                            `multiple_plants`, `ambiguous_symptoms`,
 *                            `other`.
 *  - `note`                : optional free-text (≤ 2000 chars).
 *  - `predictedClass`      : the model's top-1 class at the time of
 *                            the diagnosis. Lets the server compute
 *                            per-class accuracy server-side.
 *  - `predictedConfidence` : the confidence the model reported for
 *                            its top-1 class (0..1).
 *  - `top3`                : the model's top-3 (class, prob) pairs.
 *  - `modelVersion`        : the classifier version that produced the
 *                            scan (e.g. `2.2`).
 *  - `appVersion`          : BuildConfig version name of this app.
 *  - `locale`              : the user's UI locale (`en` / `ru`).
 *  - `platform`            : `android` (constant, but explicit so the
 *                            server can shard by platform in metrics).
 *  - `userPseudoId`        : per-install opaque UUID. Lets retraining
 *                            group feedback rows from one user
 *                            without knowing who they are.
 *  - `regionCount`         : number of detector regions in the scan.
 *  - `primaryRegionIndex`  : which region the classifier used (or -1
 *                            if none).
 *  - `feedbackLatencyMs`   : wall-clock ms between scan + feedback.
 *  - `imageHash`           : optional SHA-256 of the original image.
 */
@Serializable
data class FeedbackRequest(
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String,
    @SerializedName("correct") @SerialName("correct") val correct: Boolean,
    @SerializedName("corrected_class") @SerialName("corrected_class") val correctedClass: String? = null,
    @SerializedName("reason") @SerialName("reason") val reason: String? = null,
    @SerializedName("note") @SerialName("note") val note: String? = null,
    @SerializedName("predicted_class") @SerialName("predicted_class") val predictedClass: String? = null,
    @SerializedName("predicted_confidence") @SerialName("predicted_confidence") val predictedConfidence: Float? = null,
    @SerializedName("top3") @SerialName("top3") val top3: List<TopProb>? = null,
    @SerializedName("model_version") @SerialName("model_version") val modelVersion: String? = null,
    @SerializedName("app_version") @SerialName("app_version") val appVersion: String? = null,
    @SerializedName("locale") @SerialName("locale") val locale: String? = null,
    @SerializedName("platform") @SerialName("platform") val platform: String? = null,
    @SerializedName("user_pseudo_id") @SerialName("user_pseudo_id") val userPseudoId: String? = null,
    @SerializedName("region_count") @SerialName("region_count") val regionCount: Int? = null,
    @SerializedName("primary_region_index") @SerialName("primary_region_index") val primaryRegionIndex: Int? = null,
    @SerializedName("feedback_latency_ms") @SerialName("feedback_latency_ms") val feedbackLatencyMs: Long? = null,
    @SerializedName("image_hash") @SerialName("image_hash") val imageHash: String? = null,
    // Conformal prediction set from the analyse response that
    // produced this scan. Echoed back so the server can answer
    // "was the right answer in the 90 % set?" without joining
    // against the original analyse log.
    @SerializedName("prediction_set") @SerialName("prediction_set") val predictionSet: List<String>? = null,
)

/** One entry of the model's top-3 prediction set used by the feedback payload. */
@Serializable
data class TopProb(
    @SerializedName("class") @SerialName("class") val classKey: String,
    @SerializedName("prob") @SerialName("prob") val prob: Float,
)

/** /api/feedback response. `id` is the new row id in the server's append-only DB. */
@Serializable
data class FeedbackResponse(
    @SerializedName("ok") @SerialName("ok") val ok: Boolean = false,
    @SerializedName("id") @SerialName("id") val id: Long = 0,
)
