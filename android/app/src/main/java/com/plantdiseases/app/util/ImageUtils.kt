package com.plantdiseases.app.util

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.*

object ImageUtils {

    /** Create a temp file for camera capture */
    fun createImageFile(context: Context): File {
        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val storageDir = File(context.filesDir, "plant_images").apply { mkdirs() }
        return File(storageDir, "PLANT_${timeStamp}.jpg")
    }

    /** Compress and resize image for upload */
    fun prepareImageForUpload(context: Context, imagePath: String, maxSize: Int = 1024): File {
        val original = BitmapFactory.decodeFile(imagePath)
        val rotated = rotateIfNeeded(imagePath, original)

        val ratio = maxSize.toFloat() / maxOf(rotated.width, rotated.height)
        val scaled = if (ratio < 1f) {
            Bitmap.createScaledBitmap(
                rotated,
                (rotated.width * ratio).toInt(),
                (rotated.height * ratio).toInt(),
                true
            )
        } else rotated

        val outputFile = File(context.cacheDir, "upload_${System.currentTimeMillis()}.jpg")
        FileOutputStream(outputFile).use { out ->
            scaled.compress(Bitmap.CompressFormat.JPEG, 85, out)
        }

        if (scaled !== rotated) scaled.recycle()
        if (rotated !== original) rotated.recycle()
        original.recycle()

        return outputFile
    }

    /** Copy URI to internal storage */
    fun copyUriToFile(context: Context, uri: Uri): File? {
        return try {
            val file = createImageFile(context)
            context.contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(file).use { output ->
                    input.copyTo(output)
                }
            }
            file
        } catch (e: Exception) {
            e.printStackTrace()
            null
        }
    }

    private fun rotateIfNeeded(imagePath: String, bitmap: Bitmap): Bitmap {
        val exif = ExifInterface(imagePath)
        val orientation = exif.getAttributeInt(
            ExifInterface.TAG_ORIENTATION,
            ExifInterface.ORIENTATION_NORMAL
        )
        val matrix = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> {
                matrix.postRotate(180f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_TRANSPOSE -> {
                matrix.postRotate(90f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> {
                matrix.postRotate(-90f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
            else -> return bitmap
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
    }

    fun formatTimestamp(timestamp: Long): String {
        val sdf = SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault())
        return sdf.format(Date(timestamp))
    }

    /**
     * Format timestamp as relative date ("Today", "Yesterday", "3 days ago").
     */
    fun formatRelativeDate(context: android.content.Context, timestamp: Long): String {
        val now = System.currentTimeMillis()
        val diff = now - timestamp
        val days = (diff / (1000 * 60 * 60 * 24)).toInt()

        return when {
            days == 0 -> context.getString(com.plantdiseases.app.R.string.date_today)
            days == 1 -> context.getString(com.plantdiseases.app.R.string.date_yesterday)
            days in 2..30 -> context.getString(com.plantdiseases.app.R.string.date_days_ago, days)
            else -> formatTimestamp(timestamp)
        }
    }

    /**
     * HSV-based check: returns true if at least 8% of pixels are green-ish.
     * Green in HSV: H in [35..85], S > 0.2, V > 0.15
     */
    fun hasGreenContent(imagePath: String): Boolean {
        return try {
            val options = BitmapFactory.Options().apply {
                inSampleSize = 8 // Downsample for speed
            }
            val bitmap = BitmapFactory.decodeFile(imagePath, options) ?: return true
            val width = bitmap.width
            val height = bitmap.height
            val totalPixels = width * height
            var greenPixels = 0

            val hsv = FloatArray(3)
            for (y in 0 until height step 2) {
                for (x in 0 until width step 2) {
                    val pixel = bitmap.getPixel(x, y)
                    android.graphics.Color.colorToHSV(pixel, hsv)
                    val h = hsv[0]
                    val s = hsv[1]
                    val v = hsv[2]
                    // Green hue range: roughly 35-160 degrees, with decent saturation and brightness
                    if (h in 35f..160f && s > 0.2f && v > 0.15f) {
                        greenPixels++
                    }
                }
            }
            bitmap.recycle()

            val sampledPixels = (totalPixels / 4) // step 2 in both dimensions
            val greenRatio = greenPixels.toFloat() / sampledPixels.coerceAtLeast(1)
            greenRatio >= 0.08f
        } catch (e: Exception) {
            true // On error, assume it's a plant
        }
    }
}
