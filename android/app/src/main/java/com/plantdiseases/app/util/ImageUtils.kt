package com.plantdiseases.app.util

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
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
     * truncated download, etc.). UI maps the type to a fixed localized message
     * and never displays the exception text.
     */
    class ImageDecodeException(message: String, cause: Throwable? = null) : IOException(message, cause)

    /** Create a temp file for camera capture */
    fun createImageFile(context: Context): File {
        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val storageDir = File(context.filesDir, "plant_images").apply { mkdirs() }
        return File(storageDir, "PLANT_$timeStamp.jpg")
    }

    /**
     * Copy a saved scan image into the device's public gallery under
     * Pictures/<albumName>. Returns true on success.
     *
     * Uses MediaStore with a RELATIVE_PATH + IS_PENDING handshake (scoped
     * storage — no permission needed). Never throws — any failure is logged
     * and reported as false so a gallery hiccup can't break the scan flow.
     */
    fun saveImageToGallery(context: Context, source: File, albumName: String): Boolean {
        if (!source.exists() || source.length() == 0L) return false
        val displayName = "PD_${System.currentTimeMillis()}.jpg"
        return try {
            val resolver = context.contentResolver
            val pending = ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, displayName)
                put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
                put(
                    MediaStore.Images.Media.RELATIVE_PATH,
                    "${Environment.DIRECTORY_PICTURES}/$albumName",
                )
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
            val uri = resolver.insert(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                pending,
            ) ?: return false
            resolver.openOutputStream(uri).use { out ->
                if (out == null) {
                    resolver.delete(uri, null, null)
                    return false
                }
                source.inputStream().use { it.copyTo(out) }
            }
            resolver.update(
                uri,
                ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) },
                null,
                null,
            )
            true
        } catch (_: Exception) {
            Log.w(TAG, "Gallery save failed")
            false
        }
    }

    /**
     * Shrink an oversized capture/import *in place* before it is handed to
     * the crop editor. Camera shots and gallery imports can be 4000px+ on a
     * side; feeding a bitmap that large to the cropper makes it lay the image
     * out taller than the visible canvas (the top spilled up under the
     * toolbar) and wastes memory. We cap the longest edge at [maxSize] using a
     * cheap power-of-two downsample, then re-encode over the same file.
     *
     * Orientation is preserved by copying the EXIF orientation tag onto the
     * re-encoded file — decoding drops EXIF, so without this a rotated phone
     * photo would display sideways in the cropper. No-op (and silently
     * tolerant of any failure) when the image is already small enough or
     * cannot be read; the caller still gets a usable file either way.
     */
    fun downscaleForCropInPlace(imagePath: String, maxSize: Int = 2048) {
        try {
            val boundsOpts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(imagePath, boundsOpts)
            val rawW = boundsOpts.outWidth
            val rawH = boundsOpts.outHeight
            if (rawW <= 0 || rawH <= 0) return
            if (rawW <= maxSize && rawH <= maxSize) return

            // Preserve the original orientation tag — re-encoding strips EXIF.
            val orientation = try {
                ExifInterface(imagePath).getAttribute(ExifInterface.TAG_ORIENTATION)
            } catch (_: IOException) {
                Log.w(TAG, "EXIF read failed before crop downscale")
                null
            }

            // Two-pass: cheap power-of-two downsample to just above the cap,
            // then an exact scale down to it. inSampleSize alone is too coarse
            // (a 4000px image only steps to 2000px), so the second pass
            // guarantees the longest edge actually lands at maxSize.
            var inSampleSize = 1
            while (rawW / (inSampleSize * 2) >= maxSize || rawH / (inSampleSize * 2) >= maxSize) {
                inSampleSize *= 2
            }
            // Decode with an OOM fallback: a 4000 px capture lands on
            // inSampleSize = 1 (a ~48 MB ARGB bitmap) and can OOM the decode on
            // a low-RAM device before we ever get to scale it down. On failure
            // we double the sampling and retry, so the worst case is a slightly
            // softer pre-crop image rather than a crash on the capture path.
            val decoded = run {
                var s = inSampleSize
                var bmp: Bitmap? = null
                while (s <= 32) {
                    try {
                        bmp = BitmapFactory.decodeFile(
                            imagePath,
                            BitmapFactory.Options().apply { inSampleSize = s },
                        )
                        break
                    } catch (_: OutOfMemoryError) {
                        Log.w(TAG, "OOM in crop downscale; retrying smaller")
                        s *= 2
                    }
                }
                bmp
            } ?: return
            try {
                val ratio = maxSize.toFloat() / maxOf(decoded.width, decoded.height)
                val scaled = if (ratio < 1f) {
                    Bitmap.createScaledBitmap(
                        decoded,
                        (decoded.width * ratio).toInt().coerceAtLeast(1),
                        (decoded.height * ratio).toInt().coerceAtLeast(1),
                        true,
                    )
                } else {
                    decoded
                }
                try {
                    FileOutputStream(imagePath).use { stream ->
                        scaled.compress(Bitmap.CompressFormat.JPEG, 92, stream)
                    }
                } finally {
                    if (scaled !== decoded) scaled.recycle()
                }
            } finally {
                decoded.recycle()
            }

            if (orientation != null) {
                try {
                    ExifInterface(imagePath).apply {
                        setAttribute(ExifInterface.TAG_ORIENTATION, orientation)
                        saveAttributes()
                    }
                } catch (_: IOException) {
                    Log.w(TAG, "EXIF orientation re-write failed after crop downscale")
                }
            }
        } catch (_: IOException) {
            Log.w(TAG, "Crop downscale failed")
        } catch (_: OutOfMemoryError) {
            Log.w(TAG, "Crop downscale ran out of memory")
        }
    }

    /**
     * Copy URI to internal storage. Returns null on I/O failure;
     * the cause is logged so the user-visible toast can stay simple.
     *
     * Must be called off the UI thread — a large or cloud-backed
     * (e.g. Google Photos) source can block while the bytes stream in.
     */
    fun copyUriToFile(context: Context, uri: Uri): File? {
        return try {
            val file = createImageFile(context)
            context.contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(file).use { output ->
                    input.copyTo(output)
                }
            } ?: run {
                Log.w(TAG, "Image import returned no stream")
                return null
            }
            file
        } catch (_: IOException) {
            Log.w(TAG, "Image import failed")
            null
        } catch (_: SecurityException) {
            Log.w(TAG, "Image import permission denied")
            null
        }
    }

    /** Apply an EXIF [orientation] to [bitmap], returning a new upright bitmap
     *  (or the original when no transform is needed). */
    internal fun applyExifOrientation(orientation: Int, bitmap: Bitmap): Bitmap {
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

    /** Longest edge the cropper works at. Matches the cap the old pre-crop
     *  re-encode pass used, so the crop output size downstream is unchanged. */
    const val CROP_MAX_EDGE = 2048

    private const val MAX_DECODE_SAMPLE = 32

    /**
     * Largest power-of-two `inSampleSize` that still leaves the decoded long
     * edge at or above [maxEdge] (1 when the image is already within it).
     * The remaining exact shrink to [maxEdge] is a single scale of a bitmap
     * that is at most 2x too big, instead of a full-resolution decode.
     */
    internal fun cropSampleSize(width: Int, height: Int, maxEdge: Int = CROP_MAX_EDGE): Int {
        var sample = 1
        val longEdge = maxOf(width, height)
        while (longEdge / (sample * 2) >= maxEdge) sample *= 2
        return sample
    }

    /**
     * Decode [uri] to a software (ARGB_8888) bitmap with its EXIF orientation
     * baked in, long edge capped at [maxEdge] - ready to display and crop.
     * Used by the custom cropper.
     *
     * Large inputs (gallery picks, some camera sensors) are decoded with a
     * power-of-two `inSampleSize` and then scaled once to exactly [maxEdge],
     * before rotation, so no full-resolution bitmap and no re-encode pass is
     * needed. Images within the cap decode untouched.
     *
     * Decodes via [BitmapFactory] rather than an image loader on purpose: a
     * HARDWARE-config bitmap can't be read back or used as a `createBitmap`
     * crop source, and doing the EXIF rotation here keeps orientation
     * deterministic (applied exactly once). Must run off the UI thread — a
     * large or cloud-backed source can block while the bytes stream in.
     * Returns null on any decode failure.
     */
    fun decodeUpright(context: Context, uri: Uri, maxEdge: Int = CROP_MAX_EDGE): Bitmap? {
        return try {
            val resolver = context.contentResolver
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

            var sample = cropSampleSize(bounds.outWidth, bounds.outHeight, maxEdge)
            var decoded: Bitmap? = null
            while (decoded == null && sample <= MAX_DECODE_SAMPLE) {
                try {
                    val opts = BitmapFactory.Options().apply {
                        inPreferredConfig = Bitmap.Config.ARGB_8888
                        inSampleSize = sample
                    }
                    val stream = resolver.openInputStream(uri) ?: return null
                    decoded = stream.use { BitmapFactory.decodeStream(it, null, opts) }
                    if (decoded == null) return null
                } catch (_: OutOfMemoryError) {
                    Log.w(TAG, "OOM in crop decode; retrying smaller")
                    sample *= 2
                }
            }
            val raw = decoded ?: return null

            val ratio = maxEdge.toFloat() / maxOf(raw.width, raw.height)
            val capped = if (ratio < 1f) {
                Bitmap.createScaledBitmap(
                    raw,
                    (raw.width * ratio).toInt().coerceAtLeast(1),
                    (raw.height * ratio).toInt().coerceAtLeast(1),
                    true,
                ).also { if (it !== raw) raw.recycle() }
            } else {
                raw
            }

            val orientation = resolver.openInputStream(uri)?.use { input ->
                ExifInterface(input).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL
                )
            } ?: ExifInterface.ORIENTATION_NORMAL
            val upright = applyExifOrientation(orientation, capped)
            if (upright !== capped) capped.recycle()
            upright
        } catch (_: IOException) {
            Log.w(TAG, "Image decode failed")
            null
        } catch (_: OutOfMemoryError) {
            Log.w(TAG, "Image decode ran out of memory")
            null
        }
    }

    fun formatTimestamp(timestamp: Long): String {
        val sdf = SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault())
        return sdf.format(Date(timestamp))
    }

    /**
     * Relative age with sub-day granularity ("Just now", "5 min ago",
     * "2 hr ago"), handing off to [formatRelativeDate] from a day out so a
     * fresh scan reads as "2 hr ago" instead of a wall-clock stamp. Future
     * timestamps (clock skew) clamp to "Just now" rather than going negative.
     */
    fun formatRelativeTime(context: android.content.Context, timestamp: Long): String {
        val diff = System.currentTimeMillis() - timestamp
        val minutes = diff / (1000 * 60)
        val hours = diff / (1000 * 60 * 60)

        return when {
            minutes < 1 -> context.getString(com.plantdiseases.app.R.string.time_just_now)
            minutes < 60 ->
                context.getString(com.plantdiseases.app.R.string.time_minutes_ago, minutes.toInt())
            hours < 24 ->
                context.getString(com.plantdiseases.app.R.string.time_hours_ago, hours.toInt())
            else -> formatRelativeDate(context, timestamp)
        }
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
     * Variance-of-Laplacian focus score on the server's statistic
     * (see [BlurGate]). Lower means blurrier; compare against
     * [BlurGate.VARIANCE_LAPLACIAN_FLOOR]. Returns [Double.MAX_VALUE] on
     * any decode failure (assume sharp).
     */
    fun computeBlurScore(imagePath: String): Double = BlurGate.score(imagePath)
}
