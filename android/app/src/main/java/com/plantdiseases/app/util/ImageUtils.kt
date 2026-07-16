package com.plantdiseases.app.util

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
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
        return File(storageDir, "PLANT_$timeStamp.jpg")
    }

    /**
     * Copy a saved scan image into the device's public gallery under
     * Pictures/<albumName>. Returns true on success.
     *
     * On Android 10+ (scoped storage) this uses MediaStore with a
     * RELATIVE_PATH + IS_PENDING handshake and needs NO permission. On
     * Android 9 and below it writes into the public Pictures directory and
     * triggers a media scan, which requires WRITE_EXTERNAL_STORAGE (the
     * Profile toggle requests it before enabling autosave on those API
     * levels). Never throws — any failure is logged and reported as false so
     * a gallery hiccup can't break the scan flow.
     */
    fun saveImageToGallery(context: Context, source: File, albumName: String): Boolean {
        if (!source.exists() || source.length() == 0L) return false
        val displayName = "PD_${System.currentTimeMillis()}.jpg"
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
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
            } else {
                @Suppress("DEPRECATION")
                val picturesDir = File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES),
                    albumName,
                ).apply { mkdirs() }
                val dest = File(picturesDir, displayName)
                source.copyTo(dest, overwrite = true)
                MediaScannerConnection.scanFile(
                    context,
                    arrayOf(dest.absolutePath),
                    arrayOf("image/jpeg"),
                    null,
                )
                true
            }
        } catch (e: Exception) {
            Log.w(TAG, "Gallery save failed", e)
            false
        }
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

        // Track every intermediate bitmap so the finally can recycle them
        // even when rotation or scaling throws OutOfMemoryError partway
        // through. The previous structure only armed the recycle guard
        // *after* rotateIfNeeded(), so an OOM inside it leaked `original`.
        // Nullable trackers used only by the finally block; the non-null
        // `rot` / `out` locals below keep the hot path smart-cast clean.
        var rotated: Bitmap? = null
        var scaled: Bitmap? = null
        try {
            val rot = try {
                rotateIfNeeded(imagePath, original)
            } catch (e: IOException) {
                Log.w(TAG, "EXIF read failed, using unrotated bitmap", e)
                original
            }
            rotated = rot

            val ratio = maxSize.toFloat() / maxOf(rot.width, rot.height)
            val out = if (ratio < 1f) {
                Bitmap.createScaledBitmap(
                    rot,
                    (rot.width * ratio).toInt().coerceAtLeast(1),
                    (rot.height * ratio).toInt().coerceAtLeast(1),
                    true
                )
            } else {
                rot
            }
            scaled = out

            val outputFile = File(context.cacheDir, "upload_${System.currentTimeMillis()}.jpg")
            FileOutputStream(outputFile).use { stream ->
                out.compress(Bitmap.CompressFormat.JPEG, 85, stream)
            }
            return outputFile
        } finally {
            scaled?.let { if (it !== rotated) it.recycle() }
            rotated?.let { if (it !== original) it.recycle() }
            original.recycle()
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
            } catch (e: IOException) {
                Log.w(TAG, "EXIF read failed before crop downscale", e)
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
                    } catch (e: OutOfMemoryError) {
                        Log.w(TAG, "OOM in crop downscale at sample=$s; retrying smaller", e)
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
                } catch (e: IOException) {
                    Log.w(TAG, "EXIF orientation re-write failed after crop downscale", e)
                }
            }
        } catch (e: IOException) {
            Log.w(TAG, "downscaleForCropInPlace failed for $imagePath", e)
        } catch (e: OutOfMemoryError) {
            Log.w(TAG, "downscaleForCropInPlace OOM for $imagePath", e)
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
        val orientation = ExifInterface(imagePath).getAttributeInt(
            ExifInterface.TAG_ORIENTATION,
            ExifInterface.ORIENTATION_NORMAL
        )
        return applyExifOrientation(orientation, bitmap)
    }

    /** Apply an EXIF [orientation] to [bitmap], returning a new upright bitmap
     *  (or the original when no transform is needed). */
    private fun applyExifOrientation(orientation: Int, bitmap: Bitmap): Bitmap {
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

    /**
     * Decode [uri] to a software (ARGB_8888) bitmap with its EXIF orientation
     * baked in — ready to display and crop. Used by the custom cropper.
     *
     * Decodes via [BitmapFactory] rather than an image loader on purpose: a
     * HARDWARE-config bitmap can't be read back or used as a `createBitmap`
     * crop source, and doing the EXIF rotation here keeps orientation
     * deterministic (applied exactly once). Must run off the UI thread — a
     * large or cloud-backed source can block while the bytes stream in.
     * Returns null on any decode failure.
     */
    fun decodeUpright(context: Context, uri: Uri): Bitmap? {
        return try {
            val opts = BitmapFactory.Options().apply { inPreferredConfig = Bitmap.Config.ARGB_8888 }
            val decoded = context.contentResolver.openInputStream(uri)?.use { input ->
                BitmapFactory.decodeStream(input, null, opts)
            } ?: return null
            val orientation = context.contentResolver.openInputStream(uri)?.use { input ->
                ExifInterface(input).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL
                )
            } ?: ExifInterface.ORIENTATION_NORMAL
            val upright = applyExifOrientation(orientation, decoded)
            if (upright !== decoded) decoded.recycle()
            upright
        } catch (e: IOException) {
            Log.w(TAG, "decodeUpright failed for $uri", e)
            null
        } catch (e: OutOfMemoryError) {
            Log.w(TAG, "decodeUpright OOM for $uri", e)
            null
        }
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
     * SHA-256 of [file]'s bytes, lowercase hex. Returns null when the
     * file is missing or unreadable — callers (feedback submission)
     * treat the hash as optional and must not block on it.
     *
     * Streams 8 KiB buffers so large images don't materialise in
     * memory. Must be called off the UI thread.
     */
    fun sha256Hex(file: File): String? = try {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(8 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        digest.digest().joinToString("") { "%02x".format(it) }
    } catch (e: IOException) {
        Log.w(TAG, "sha256Hex failed for ${file.path}", e)
        null
    } catch (e: SecurityException) {
        Log.w(TAG, "sha256Hex: permission denied for ${file.path}", e)
        null
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

            // Convert to grayscale in place — reuse the pixel buffer rather than
            // allocating a second full-size IntArray (saves ~w*h*4 bytes).
            for (i in pixels.indices) {
                val pixel = pixels[i]
                val r = (pixel shr 16) and 0xFF
                val g = (pixel shr 8) and 0xFF
                val b = pixel and 0xFF
                pixels[i] = (0.299 * r + 0.587 * g + 0.114 * b).toInt()
            }

            // Apply Laplacian kernel [0,1,0; 1,-4,1; 0,1,0] and compute variance
            var sum = 0.0
            var sumSq = 0.0
            var count = 0
            for (y in 1 until h - 1) {
                for (x in 1 until w - 1) {
                    val laplacian = pixels[(y - 1) * w + x] +
                        pixels[(y + 1) * w + x] +
                        pixels[y * w + (x - 1)] +
                        pixels[y * w + (x + 1)] -
                        4 * pixels[y * w + x]
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
}
