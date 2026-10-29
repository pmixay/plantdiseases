package com.plantdiseases.app.util

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import androidx.exifinterface.media.ExifInterface
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest

data class SanitizedImage(
    val bytes: ByteArray,
    val width: Int,
    val height: Int,
    val filename: String = GENERIC_UPLOAD_NAME,
    val contentType: String = "image/jpeg",
) {
    val sha256: String by lazy {
        MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    companion object {
        const val GENERIC_UPLOAD_NAME = "scan.jpg"
    }
}

interface UploadImageSanitizer {
    fun sanitize(
        source: File,
        maxDimension: Int = DefaultUploadImageSanitizer.DEFAULT_MAX_DIMENSION,
        quality: Int = DefaultUploadImageSanitizer.DEFAULT_QUALITY,
    ): SanitizedImage
}

/** Decode → orient pixels → fresh opaque bitmap → bounded metadata-free JPEG. */
object DefaultUploadImageSanitizer : UploadImageSanitizer {
    const val DEFAULT_MAX_DIMENSION = 1_024
    const val DEFAULT_QUALITY = 85
    const val MAX_OUTPUT_BYTES = 4 * 1_024 * 1_024
    private const val MAX_DECODED_PIXELS = 40_000_000L
    private const val MAX_SOURCE_DIMENSION = 16_384

    private fun invalidImage(message: String): Nothing =
        throw ImageUtils.ImageDecodeException(message)

    override fun sanitize(source: File, maxDimension: Int, quality: Int): SanitizedImage = try {
        sanitizeChecked(source, maxDimension, quality)
    } catch (error: OutOfMemoryError) {
        throw ImageUtils.ImageDecodeException("Image is too large to sanitize safely", error)
    }

    private fun sanitizeChecked(source: File, maxDimension: Int, quality: Int): SanitizedImage {
        require(maxDimension in 1..4_096)
        require(quality in 40..95)
        if (!source.isFile) invalidImage("Image is unavailable")

        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(source.absolutePath, bounds)
        val rawWidth = bounds.outWidth
        val rawHeight = bounds.outHeight
        if (rawWidth <= 0 || rawHeight <= 0) {
            invalidImage("Image is empty or unsupported")
        }
        if (
            rawWidth > MAX_SOURCE_DIMENSION ||
            rawHeight > MAX_SOURCE_DIMENSION ||
            rawWidth.toLong() * rawHeight.toLong() > MAX_DECODED_PIXELS
        ) {
            invalidImage("Image dimensions are too large")
        }

        var sample = 1
        while (rawWidth / (sample * 2) >= maxDimension || rawHeight / (sample * 2) >= maxDimension) {
            sample *= 2
        }
        val decoded = BitmapFactory.decodeFile(
            source.absolutePath,
            BitmapFactory.Options().apply {
                inSampleSize = sample
                inPreferredConfig = Bitmap.Config.ARGB_8888
            },
        ) ?: invalidImage("Image could not be decoded")

        var upright: Bitmap? = null
        var fresh: Bitmap? = null
        try {
            val orientation = runCatching {
                ExifInterface(source.absolutePath).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL,
                )
            }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
            val oriented = ImageUtils.applyExifOrientation(orientation, decoded)
            upright = oriented

            val ratio = minOf(1f, maxDimension.toFloat() / maxOf(oriented.width, oriented.height))
            val outputWidth = (oriented.width * ratio).toInt().coerceAtLeast(1)
            val outputHeight = (oriented.height * ratio).toInt().coerceAtLeast(1)
            val clean = Bitmap.createBitmap(outputWidth, outputHeight, Bitmap.Config.ARGB_8888)
            fresh = clean
            Canvas(clean).apply {
                drawColor(Color.WHITE)
                drawBitmap(
                    oriented,
                    null,
                    Rect(0, 0, outputWidth, outputHeight),
                    Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG),
                )
            }

            var encodeQuality = quality
            var encoded: ByteArray
            do {
                val output = ByteArrayOutputStream()
                check(clean.compress(Bitmap.CompressFormat.JPEG, encodeQuality, output))
                encoded = output.toByteArray()
                encodeQuality -= 10
            } while (encoded.size > MAX_OUTPUT_BYTES && encodeQuality >= 40)
            if (encoded.size > MAX_OUTPUT_BYTES) {
                invalidImage("Sanitized image is too large")
            }
            return SanitizedImage(encoded, outputWidth, outputHeight)
        } finally {
            fresh?.recycle()
            upright?.let { if (it !== decoded) it.recycle() }
            decoded.recycle()
        }
    }
}
