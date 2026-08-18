package com.plantdiseases.app.ui.result

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.util.Log
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.ImageUtils
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream

/**
 * Builds and fires the "share this diagnosis" intent: a composed JPEG (the
 * scan photo with the diseased region highlighted) plus a text summary, with a
 * graceful text-only fallback when the image can't be decoded/composed.
 */
class ShareController(
    private val activity: AppCompatActivity,
    private val scanRepository: ScanRepository,
) {

    fun share(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val shareText = buildString {
            append(activity.getString(R.string.share_diagnosis_header))
            append("\n\n")
            append(if (isRu) scan.diseaseNameRu else scan.diseaseName)
            append(" (${(scan.confidence * 100).toInt()}%)\n\n")
            append(if (isRu) scan.descriptionRu else scan.description)
            val treatmentList = repository.parseStringList(if (isRu) scan.treatmentRu else scan.treatment)
            if (treatmentList.isNotEmpty()) {
                append("\n\n")
                append(activity.getString(R.string.share_treatment_label))
                append("\n")
                treatmentList.forEachIndexed { i, t -> append("${i + 1}. $t\n") }
            }
        }

        val imageFile = File(scan.imagePath)
        if (!imageFile.exists()) {
            launchShareTextOnly(shareText)
            return
        }

        // Image composition (decode + canvas + JPEG encode) is off the UI thread.
        activity.lifecycleScope.launch {
            val shareFile = try {
                withContext(Dispatchers.IO) { createShareableImage(scan) }
            } catch (_: Exception) {
                Log.w(TAG, "Share image composition failed; using text only")
                null
            }

            if (shareFile == null || activity.isFinishing) {
                if (!activity.isFinishing) launchShareTextOnly(shareText)
                return@launch
            }

            val imageUri = try {
                FileProvider.getUriForFile(activity, "${activity.packageName}.fileprovider", shareFile)
            } catch (_: IllegalArgumentException) {
                Log.w(TAG, "Share URI failed; using text only")
                launchShareTextOnly(shareText)
                return@launch
            }

            val sendIntent = Intent().apply {
                action = Intent.ACTION_SEND
                putExtra(Intent.EXTRA_TEXT, shareText)
                putExtra(Intent.EXTRA_STREAM, imageUri)
                type = "image/jpeg"
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            activity.startActivity(Intent.createChooser(sendIntent, activity.getString(R.string.share)))
        }
    }

    private fun launchShareTextOnly(shareText: String) {
        val sendIntent = Intent().apply {
            action = Intent.ACTION_SEND
            putExtra(Intent.EXTRA_TEXT, shareText)
            type = "text/plain"
        }
        activity.startActivity(Intent.createChooser(sendIntent, activity.getString(R.string.share)))
    }

    private fun createShareableImage(scan: ScanEntity): File {
        activity.cacheDir.listFiles { file -> file.name.startsWith("share_") }?.forEach { it.delete() }

        val maxDim = 1920
        val boundsOpts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(scan.imagePath, boundsOpts)
        val rawW = boundsOpts.outWidth
        val rawH = boundsOpts.outHeight
        if (rawW <= 0 || rawH <= 0) {
            throw ImageUtils.ImageDecodeException("Source image cannot be decoded")
        }
        var inSampleSize = 1
        while (rawW / (inSampleSize * 2) >= maxDim && rawH / (inSampleSize * 2) >= maxDim) {
            inSampleSize *= 2
        }
        val decodeOpts = BitmapFactory.Options().apply {
            this.inSampleSize = inSampleSize
            inMutable = true
        }
        val result = BitmapFactory.decodeFile(scan.imagePath, decodeOpts)
            ?: throw ImageUtils.ImageDecodeException("Failed to decode image")

        val finalBitmap = if (maxOf(result.width, result.height) > maxDim) {
            val ratio = maxDim.toFloat() / maxOf(result.width, result.height)
            val scaled = Bitmap.createScaledBitmap(
                result,
                (result.width * ratio).toInt(),
                (result.height * ratio).toInt(),
                true,
            )
            result.recycle()
            scaled
        } else {
            result
        }

        // Skip the red highlight when the verdict is healthy.
        if (!scanRepository.isHealthyVerdict(scan)) {
            val regions = scanRepository.parseRegions(scan.regions)
            val primary = regions.getOrNull(scan.primaryRegionIndex ?: -1)
            if (primary != null) {
                // Bbox coordinates live in the server's decoded image space
                // (the <=1024 px upload the server echoes back), NOT the
                // on-disk original — so scale against the detection dims to
                // match the on-screen overlay. Fall back to the raw dims for
                // legacy scans that predate detection-dimension persistence.
                val detW = scan.detectionImageWidth.takeIf { it > 0 } ?: rawW
                val detH = scan.detectionImageHeight.takeIf { it > 0 } ?: rawH
                val scaleX = finalBitmap.width.toFloat() / detW
                val scaleY = finalBitmap.height.toFloat() / detH
                val canvas = Canvas(finalBitmap)
                val cx = (primary.x + primary.width / 2f) * scaleX
                val cy = (primary.y + primary.height / 2f) * scaleY
                val radius = (primary.width + primary.height) / 4f * ((scaleX + scaleY) / 2f)

                val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG)
                val gradient = android.graphics.RadialGradient(
                    cx,
                    cy,
                    radius.coerceAtLeast(1f),
                    intArrayOf(
                        android.graphics.Color.argb(120, 255, 0, 0),
                        android.graphics.Color.argb(80, 255, 165, 0),
                        android.graphics.Color.argb(40, 255, 255, 0),
                        android.graphics.Color.TRANSPARENT,
                    ),
                    floatArrayOf(0f, 0.4f, 0.7f, 1f),
                    android.graphics.Shader.TileMode.CLAMP,
                )
                paint.shader = gradient
                canvas.drawCircle(cx, cy, radius, paint)
            }
        }

        val shareFile = File(activity.cacheDir, "share_${System.currentTimeMillis()}.jpg")
        FileOutputStream(shareFile).use { out ->
            finalBitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
        }
        finalBitmap.recycle()
        return shareFile
    }

    private companion object {
        private const val TAG = "ResultShare"
    }
}
