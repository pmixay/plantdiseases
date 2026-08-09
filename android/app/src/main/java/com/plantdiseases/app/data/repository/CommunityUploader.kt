package com.plantdiseases.app.data.repository

import android.content.Context
import android.util.Log
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.UploadImageSanitizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Glue between a freshly-saved [ScanEntity] and the server's community
 * scan corpus.
 *
 * Responsibilities, in order:
 *   1. Confirm uploads are enabled (opt-in, default OFF — the user
 *      enables community in-context; see [CommunityPrefs]).
 *   2. Load the exact metadata-free JPEG bytes submitted to /api/analyze.
 *      Publication is SHA-bound, so a second JPEG encode is invalid.
 *   3. POST the row + retained image via [ScanRepository.uploadCommunityScan].
 *      Location is client-supplied (coarse country/city + rounded coords);
 *      there is no server-side IP geolocation. Only scans with a
 *      server-issued scan_id are shared — on-device Aqua scans stay private.
 *
 * Every step is best-effort: failures are logged and swallowed so the
 * user's diagnosis screen renders even when the corpus is down.
 */
class CommunityUploader(
    private val context: Context,
    private val scanRepository: ScanRepository,
    private val authRepository: AuthRepository,
    // Source-compatible until the independently owned DI module is updated.
    // Never call this here: a second encode cannot satisfy the server's
    // byte-for-byte /api/analyze publication binding.
    @Suppress("UNUSED_PARAMETER")
    imageSanitizer: UploadImageSanitizer = DefaultUploadImageSanitizer,
) {
    suspend fun uploadIfEnabled(
        scan: ScanEntity,
        analysis: AnalysisResponse,
    ): UploadOutcome = withContext(Dispatchers.IO) {
        if (!CommunityPrefs.uploadsEnabled(context)) {
            return@withContext UploadOutcome.OptedOut
        }
        if (authRepository.state.value !is AuthState.Authenticated) {
            return@withContext UploadOutcome.AuthRequired
        }
        // On-device (Aqua) scans never hit /api/analyze, so they carry no
        // server-issued scan_id. Those stay private by default — we don't
        // share them to the community, and the server would reject them
        // anyway (it only accepts scan_ids it issued). A blank scan_id is the
        // definitive "on-device / offline" signal.
        if (analysis.scanId.isNullOrBlank()) {
            return@withContext UploadOutcome.NotServerBacked
        }
        val disease = canonicalDiseaseKey(analysis) ?: scan.diseaseName
        // Skip diagnoses that shouldn't be surfaced in the community
        // feed (currently just ``not_a_plant`` — typically faces,
        // objects, or random photos that other users have no reason
        // to browse). The server already filters these on read; this
        // saves the upload bandwidth + the row + the thumb file on
        // disk for an entry no one will ever see.
        if (disease in HIDDEN_COMMUNITY_DISEASES) {
            return@withContext UploadOutcome.HiddenDisease
        }
        val species = analysis.species?.best?.scientificName
        val score = analysis.species?.best?.score?.takeIf { it > 0f }
        val locale = if (LocaleHelper.isRussian(context)) "ru" else "en"
        val scanId = scan.clientScanId.ifBlank { return@withContext UploadOutcome.MissingScanId }
        if (analysis.scanId != scanId) {
            return@withContext UploadOutcome.MissingScanId
        }
        val analysisImage = scanRepository.retainedAnalysisImage(scan)
            ?: return@withContext UploadOutcome.MissingAnalysisImage
        // Opt-in is confirmed above, so reading the coarse location here is the
        // "check user location on every scan" step — attaches where the scan
        // was taken so it can be grouped by the 30 km nearby radius.
        val region = runCatching { RegionResolver.resolveRegion(context) }.getOrNull()

        val result = scanRepository.uploadCommunityScan(
            scanId = scanId,
            country = region?.country,
            region = region?.locality.orEmpty(),
            lat = region?.lat,
            lon = region?.lon,
            disease = disease,
            confidence = analysis.confidence,
            species = species,
            speciesScore = score,
            locale = locale,
            analysisImage = analysisImage,
        )
        result.fold(
            onSuccess = { UploadOutcome.Uploaded },
            onFailure = {
                Log.i(TAG, "Community upload skipped")
                UploadOutcome.Failed
            },
        )
    }

    /**
     * Upload one already-saved scan on demand, deriving everything from
     * the persisted [ScanEntity] (no live [AnalysisResponse] needed).
     *
     * Used when the scanner explicitly asks for a second opinion: that
     * tap is consent to share THIS scan, so [force] bypasses the global
     * uploads-enabled toggle. The disease key is the displayed verdict
     * ([ScanEntity.displayedClassKey]) — the same reroute-aware key the
     * normal upload path uses — so the shared row matches what the user
     * saw. The server dedups by scan_id, so calling this for a scan that
     * already uploaded is a harmless no-op.
     */
    suspend fun uploadFromEntity(
        scan: ScanEntity,
        force: Boolean = false,
    ): UploadOutcome = withContext(Dispatchers.IO) {
        if (!force && !CommunityPrefs.uploadsEnabled(context)) {
            return@withContext UploadOutcome.OptedOut
        }
        if (authRepository.state.value !is AuthState.Authenticated) {
            return@withContext UploadOutcome.AuthRequired
        }
        // On-device (Aqua) scans are private by default — an offline scan has
        // no server-issued scan_id, so sharing it would only send the image
        // for a guaranteed server-side rejection. The persisted ``offline_aqua``
        // warning is the entity-side marker for those.
        if ("offline_aqua" in scanRepository.parseWarnings(scan.warnings)) {
            return@withContext UploadOutcome.NotServerBacked
        }
        val disease = scan.displayedClassKey?.takeIf { it.isNotBlank() }
            ?: scan.diseaseName
        if (disease in HIDDEN_COMMUNITY_DISEASES) {
            return@withContext UploadOutcome.HiddenDisease
        }
        val scanId = scan.clientScanId.ifBlank { return@withContext UploadOutcome.MissingScanId }
        val analysisImage = scanRepository.retainedAnalysisImage(scan)
            ?: return@withContext UploadOutcome.MissingAnalysisImage
        val locale = if (LocaleHelper.isRussian(context)) "ru" else "en"
        val region = runCatching { RegionResolver.resolveRegion(context) }.getOrNull()
        val result = scanRepository.uploadCommunityScan(
            scanId = scanId,
            country = region?.country,
            region = region?.locality.orEmpty(),
            lat = region?.lat,
            lon = region?.lon,
            disease = disease,
            confidence = scan.confidence,
            species = scan.speciesScientific,
            speciesScore = scan.speciesScore,
            locale = locale,
            analysisImage = analysisImage,
        )
        result.fold(
            onSuccess = { UploadOutcome.Uploaded },
            onFailure = {
                Log.i(TAG, "Community on-demand upload skipped")
                UploadOutcome.Failed
            },
        )
    }

    /** Outcome of one upload attempt — surfaced to callers only for
     *  logging; never user-visible. */
    enum class UploadOutcome {
        Uploaded,
        OptedOut,
        MissingScanId,
        MissingAnalysisImage,
        Failed,
        HiddenDisease,
        AuthRequired,

        // On-device (Aqua/offline) scan with no server-issued scan_id — kept
        // private, never shared to the community.
        NotServerBacked,
    }

    /**
     * The server's CommunityScanRequest constrains ``disease`` to the
     * known class set (see ``_FEEDBACK_ALLOWED_CORRECTIONS`` in
     * main.py). The scan's stored ``diseaseName`` is a localised
     * display string; we want the canonical key.
     *
     * Prefer the server's DISPLAYED verdict key (``classKey`` /
     * ``class_name``) — it already reflects the weak-healthy reroute /
     * healthy-fallback / OOD gate, so the community row carries the same
     * disease the user actually saw. Using ``argmax(all_probs)`` here
     * instead would store the RAW top class (e.g. "healthy") for a scan
     * the user was shown as a disease, so "same disease" matching could
     * never match it. Fall back to the raw argmax, then to a normalised
     * display name, only when the verdict key is absent (legacy/partial
     * responses).
     */
    private fun canonicalDiseaseKey(analysis: AnalysisResponse): String? {
        analysis.classKey?.takeIf { it.isNotBlank() }?.let { return it }
        val byProbs = analysis.allProbs?.entries?.maxByOrNull { it.value }?.key
        if (!byProbs.isNullOrBlank()) return byProbs
        return analysis.diseaseName
            .lowercase()
            .replace(' ', '_')
            .replace("(", "")
            .replace(")", "")
            .takeIf { it.isNotBlank() }
    }

    companion object {
        private const val TAG = "CommunityUploader"

        // Mirrors the server's ``_HIDDEN_COMMUNITY_DISEASES`` set —
        // diagnoses we never surface in the community feed and so
        // shouldn't upload either.
        private val HIDDEN_COMMUNITY_DISEASES = setOf("not_a_plant")
    }
}
