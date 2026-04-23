package com.plantdiseases.app.util

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*

object ImageUtils {

    private const val TAG = "ImageUtils"

    /**
     * Thrown when an image on disk cannot be decoded (corrupt, wrong format,
     * truncated download, etc.). The message is human-friendly so the UI can
     * surface it directly.
     */
    class ImageDecodeException(message: String, cause: Throwable? = null) : IOException(message, cause)

    /** Create a temp file for camera capture */
    fun createImageFile(context: Context): File {
        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val storageDir = File(context.filesDir, "plant_images").apply { mkdirs() }
        return File(storageDir, "PLANT_${timeStamp}.jpg")
    }

    /**
     * Compress and resize image for upload.
     *
     * Performs two-pass decoding (bounds first, then downsample) and
     * always recycles intermediate bitmaps. Throws [ImageDecodeException]
     * on unreadable files and [OutOfMemoryError] upstream if the device
     * genuinely cannot fit the image — callers should catch both.
     */
    @Throws(ImageDecodeException::class)
    fun prepareImageForUpload(context: Context, imagePath: String, maxSize: Int = 1024): File {
        val boundsOpts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imagePath, boundsOpts)
        val rawW = boundsOpts.outWidth
        val rawH = boundsOpts.outHeight
        if (rawW <= 0 || rawH <= 0) {
            throw ImageDecodeException("Image file is empty or corrupt: $imagePath")
        }

        var inSampleSize = 1
        if (rawW > maxSize || rawH > maxSize) {
            val halfW = rawW / 2
            val halfH = rawH / 2
            while (halfW / inSampleSize >= maxSize || halfH / inSampleSize >= maxSize) {
                inSampleSize *= 2
            }
        }
        val decodeOpts = BitmapFactory.Options().apply { this.inSampleSize = inSampleSize }
        val original = BitmapFactory.decodeFile(imagePath, decodeOpts)
            ?: throw ImageDecodeException("Failed to decode image: $imagePath")

        val rotated = try {
            rotateIfNeeded(imagePath, original)
        } catch (e: IOException) {
            Log.w(TAG, "EXIF read failed, using unrotated bitmap", e)
            original
        }

        val ratio = maxSize.toFloat() / maxOf(rotated.width, rotated.height)
        val scaled = if (ratio < 1f) {
            Bitmap.createScaledBitmap(
                rotated,
                (rotated.width * ratio).toInt().coerceAtLeast(1),
                (rotated.height * ratio).toInt().coerceAtLeast(1),
                true
            )
        } else rotated

        val outputFile = File(context.cacheDir, "upload_${System.currentTimeMillis()}.jpg")
        try {
            FileOutputStream(outputFile).use { out ->
                scaled.compress(Bitmap.CompressFormat.JPEG, 85, out)
            }
        } finally {
            if (scaled !== rotated) scaled.recycle()
            if (rotated !== original) rotated.recycle()
            original.recycle()
        }

        return outputFile
    }

    /**
     * Copy URI to internal storage. Returns null on I/O failure;
     * the cause is logged so the user-visible toast can stay simple.
     */
    fun copyUriToFile(context: Context, uri: Uri): File? {
        return try {
            val file = createImageFile(context)
            context.contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(file).use { output ->
                    input.copyTo(output)
                }
            } ?: run {
                Log.w(TAG, "copyUriToFile: null input stream for $uri")
                return null
            }
            file
        } catch (e: IOException) {
            Log.w(TAG, "copyUriToFile failed for $uri", e)
            null
        } catch (e: SecurityException) {
            Log.w(TAG, "copyUriToFile: permission denied for $uri", e)
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

    /** Get image dimensions without fully decoding the bitmap */
    fun getImageDimensions(imagePath: String): Pair<Int, Int> {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imagePath, options)
        return Pair(options.outWidth.coerceAtLeast(1), options.outHeight.coerceAtLeast(1))
    }

    /**
     * Compute Laplacian variance to detect blurry images.
     * Returns a variance value — lower means blurrier.
     * Threshold ~100 is a reasonable default: below it the image is likely blurry.
     */
    fun computeBlurScore(imagePath: String): Double {
        return try {
            val options = BitmapFactory.Options().apply { inSampleSize = 4 }
            val bitmap = BitmapFactory.decodeFile(imagePath, options) ?: return Double.MAX_VALUE
            val w = bitmap.width
            val h = bitmap.height

            // Bulk-read all pixels at once (20-30x faster than per-pixel getPixel)
            val pixels = IntArray(w * h)
            bitmap.getPixels(pixels, 0, w, 0, 0, w, h)
            bitmap.recycle()

            // Convert to grayscale
            val gray = IntArray(w * h)
            for (i in pixels.indices) {
                val pixel = pixels[i]
                val r = (pixel shr 16) and 0xFF
                val g = (pixel shr 8) and 0xFF
                val b = pixel and 0xFF
                gray[i] = (0.299 * r + 0.587 * g + 0.114 * b).toInt()
            }

            // Apply Laplacian kernel [0,1,0; 1,-4,1; 0,1,0] and compute variance
            var sum = 0.0
            var sumSq = 0.0
            var count = 0
            for (y in 1 until h - 1) {
                for (x in 1 until w - 1) {
                    val laplacian = gray[(y - 1) * w + x] +
                            gray[(y + 1) * w + x] +
                            gray[y * w + (x - 1)] +
                            gray[y * w + (x + 1)] -
                            4 * gray[y * w + x]
                    sum += laplacian
                    sumSq += laplacian.toDouble() * laplacian
                    count++
                }
            }
            if (count == 0) return Double.MAX_VALUE
            val mean = sum / count
            (sumSq / count) - (mean * mean) // variance
        } catch (e: OutOfMemoryError) {
            Log.w(TAG, "computeBlurScore: out of memory", e)
            Double.MAX_VALUE
        } catch (e: Exception) {
            Log.w(TAG, "computeBlurScore failed", e)
            Double.MAX_VALUE // On error, assume sharp
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
        } catch (e: OutOfMemoryError) {
            Log.w(TAG, "hasGreenContent: out of memory", e)
            true
        } catch (e: Exception) {
            Log.w(TAG, "hasGreenContent failed", e)
            true // On error, assume it's a plant
        }
    }
}
