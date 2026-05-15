package com.plantdiseases.app.data.repository

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.LocaleHelper
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
 *   1. Confirm the user has opted into uploads (default ON after the
 *      first-run consent sheet — see [CommunityPrefs]).
 *   2. Resolve the device region. No region → no upload; we never send
 *      a row without geographical grouping because the corpus would
 *      become a public dump.
 *   3. Generate a 512px JPEG thumbnail in the app's cache dir so the
 *      upload stays under the server's COMMUNITY_MAX_THUMB_BYTES
 *      ceiling.
 *   4. POST the row + thumb via [ScanRepository.uploadCommunityScan].
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
        val resolved = RegionResolver.resolveRegion(context)
            ?: return@withContext UploadOutcome.NoRegion
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

        val result = scanRepository.uploadCommunityScan(
            scanId = scanId,
            userPseudoId = pseudoId,
            country = resolved.country,
            region = resolved.locality.orEmpty(),
            disease = disease,
            confidence = analysis.confidence,
            species = species,
            speciesScore = score,
            locale = locale,
            thumbnail = thumb,
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

    /** Outcome of one upload attempt — surfaced to callers only for
     *  logging; never user-visible. */
    enum class UploadOutcome {
        Uploaded, OptedOut, NoRegion, MissingScanId, Failed, HiddenDisease,
    }

    /**
     * The server's CommunityScanRequest constrains ``disease`` to the
     * known class set (see ``_FEEDBACK_ALLOWED_CORRECTIONS`` in
     * main.py). The scan's stored ``diseaseName`` is a localised
     * display string; we want the canonical key. Prefer the
     * top-class key from ``all_probs``; fall back to a normalised
     * version of the display name as a last resort.
     */
    private fun canonicalDiseaseKey(analysis: AnalysisResponse): String? {
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
        } else decoded
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
