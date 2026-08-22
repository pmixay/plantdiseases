package com.plantdiseases.app.data.repository

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.Nicknames
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream

/**
 * Glue between a freshly-saved [ScanEntity] and the server's community
 * scan corpus.
 *
 * Responsibilities, in order:
 *   1. Confirm uploads are enabled (opt-in, default OFF — the user
 *      enables community in-context; see [CommunityPrefs]).
 *   2. Generate a 512px JPEG thumbnail in the app's cache dir so the
 *      upload stays under the server's COMMUNITY_MAX_THUMB_BYTES
 *      ceiling.
 *   3. POST the row + thumb via [ScanRepository.uploadCommunityScan].
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
) {
    suspend fun uploadIfEnabled(
        scan: ScanEntity,
        analysis: AnalysisResponse,
    ): UploadOutcome = withContext(Dispatchers.IO) {
        if (!CommunityPrefs.uploadsEnabled(context)) {
            return@withContext UploadOutcome.OptedOut
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
        val thumb = makeThumbnail(scan)
        val species = analysis.species?.best?.scientificName
        val score = analysis.species?.best?.score?.takeIf { it > 0f }
        val locale = if (LocaleHelper.isRussian(context)) "ru" else "en"
        val pseudoId = TelemetryIds.userPseudoId(context)
        val scanId = scan.clientScanId.ifBlank { return@withContext UploadOutcome.MissingScanId }
        // Opt-in is confirmed above, so reading the coarse location here is the
        // "check user location on every scan" step — attaches where the scan
        // was taken so it can be grouped by the 30 km nearby radius.
        val region = runCatching { RegionResolver.resolveRegion(context) }.getOrNull()

        // Register the durable forum identity here — on a real share, behind
        // the opt-in — rather than from Application.onCreate(), which used to
        // transmit it on every cold start before the onboarding disclosure had
        // even rendered. Best-effort: the upload also sends the same key, but
        // identity adoption failure must never block the share itself.
        scanRepository.ensureCommunityAuthorAdopted(context)

        val result = scanRepository.uploadCommunityScan(
            scanId = scanId,
            userPseudoId = pseudoId,
            country = region?.country,
            region = region?.locality.orEmpty(),
            lat = region?.lat,
            lon = region?.lon,
            disease = disease,
            confidence = analysis.confidence,
            species = species,
            speciesScore = score,
            locale = locale,
            nickname = Nicknames.nickname(context),
            thumbnail = thumb,
            context = context,
        )
        // The thumbnail is a transient cache file; delete it once the
        // upload either succeeded or hard-failed so we don't leak
        // them into the user's cache dir.
        thumb?.let { runCatching { it.delete() } }
        result.fold(
            onSuccess = { UploadOutcome.Uploaded },
            onFailure = { exc ->
                Log.i(TAG, "Community upload skipped: ${exc.message}")
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
        val thumb = makeThumbnail(scan)
        val locale = if (LocaleHelper.isRussian(context)) "ru" else "en"
        val pseudoId = TelemetryIds.userPseudoId(context)
        val region = runCatching { RegionResolver.resolveRegion(context) }.getOrNull()
        // Same lazy, share-time registration as uploadIfEnabled. On the
        // second-opinion path `force` bypasses the global toggle, and that tap
        // is itself consent to share this scan — so adopt on it too.
        scanRepository.ensureCommunityAuthorAdopted(context, force = force)
        val result = scanRepository.uploadCommunityScan(
            scanId = scanId,
            userPseudoId = pseudoId,
            country = region?.country,
            region = region?.locality.orEmpty(),
            lat = region?.lat,
            lon = region?.lon,
            disease = disease,
            confidence = scan.confidence,
            species = scan.speciesScientific,
            speciesScore = scan.speciesScore,
            locale = locale,
            nickname = Nicknames.nickname(context),
            thumbnail = thumb,
            context = context,
        )
        thumb?.let { runCatching { it.delete() } }
        result.fold(
            onSuccess = { UploadOutcome.Uploaded },
            onFailure = { exc ->
                Log.i(TAG, "Community on-demand upload skipped: ${exc.message}")
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
        Failed,
        HiddenDisease,

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

    private fun makeThumbnail(scan: ScanEntity): File? {
        val src = File(scan.imagePath)
        if (!src.exists()) return null
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(src.absolutePath, bounds)
        val srcW = bounds.outWidth.takeIf { it > 0 } ?: return null
        val srcH = bounds.outHeight.takeIf { it > 0 } ?: return null
        // Power-of-two inSampleSize so we don't decode the full image
        // off-device just to throw 95% of the pixels away.
        var sample = 1
        while ((srcW / (sample * 2)) >= THUMB_LONG_EDGE && (srcH / (sample * 2)) >= THUMB_LONG_EDGE) {
            sample *= 2
        }
        val decoded = BitmapFactory.decodeFile(
            src.absolutePath,
            BitmapFactory.Options().apply { inSampleSize = sample },
        ) ?: return null
        val scaled = if (maxOf(decoded.width, decoded.height) > THUMB_LONG_EDGE) {
            val ratio = THUMB_LONG_EDGE.toFloat() / maxOf(decoded.width, decoded.height)
            val w = (decoded.width * ratio).toInt().coerceAtLeast(1)
            val h = (decoded.height * ratio).toInt().coerceAtLeast(1)
            Bitmap.createScaledBitmap(decoded, w, h, true).also { decoded.recycle() }
        } else {
            decoded
        }
        val out = File(
            context.cacheDir,
            "community_thumb_${scan.id}_${System.currentTimeMillis()}.jpg",
        )
        return try {
            FileOutputStream(out).use { os ->
                scaled.compress(Bitmap.CompressFormat.JPEG, THUMB_QUALITY, os)
            }
            scaled.recycle()
            out
        } catch (exc: Exception) {
            Log.i(TAG, "Thumb encode failed: ${exc.message}")
            scaled.recycle()
            null
        }
    }

    companion object {
        private const val TAG = "CommunityUploader"
        private const val THUMB_LONG_EDGE = 512
        private const val THUMB_QUALITY = 78

        // Mirrors the server's ``_HIDDEN_COMMUNITY_DISEASES`` set —
        // diagnoses we never surface in the community feed and so
        // shouldn't upload either.
        private val HIDDEN_COMMUNITY_DISEASES = setOf("not_a_plant")
    }
}
