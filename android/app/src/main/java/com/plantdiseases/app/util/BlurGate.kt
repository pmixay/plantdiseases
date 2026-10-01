package com.plantdiseases.app.util

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log

/**
 * The image-quality gate, computed exactly as the server computes it.
 *
 * `server/pipeline.py:_variance_of_laplacian` converts the frame to
 * grayscale with PIL's ITU-R 601-2 integer luma, downscales the long edge to
 * [DOWNSCALE_MAX_SIDE] px, convolves the interior with the 4-neighbour
 * Laplacian and takes the variance; frames below
 * [VARIANCE_LAPLACIAN_FLOOR] abstain with `image_quality`. The Aqua tier has
 * no server round-trip, so it must reject the same photographs the gamma
 * tier rejects: same statistic, same scale, same floor. Until 2.7.5 this
 * file's predecessor scored a quarter-resolution decode (~1000 px on a
 * 12 MP frame) against a floor of 100, i.e. a different gate.
 *
 * `BlurGateTest` pins [laplacianVariance] to the server implementation on a
 * shared synthetic fixture (`server/tests/test_blur_parity.py`).
 */
object BlurGate {
    private const val TAG = "BlurGate"

    /** `pipeline.BLUR_LAPLACIAN_FLOOR`. */
    const val VARIANCE_LAPLACIAN_FLOOR = 50.0

    /** `pipeline.BLUR_DOWNSCALE`: long edge after downscaling. */
    const val DOWNSCALE_MAX_SIDE = 320

    // PIL `Image.convert("L")`: L = (R*19595 + G*38470 + B*7471 + 0x8000) >> 16.
    private const val LUMA_R = 19595
    private const val LUMA_G = 38470
    private const val LUMA_B = 7471
    private const val LUMA_ROUND = 0x8000
    private const val LUMA_SHIFT = 16
    private const val CHANNEL_MASK = 0xFF
    private const val RED_SHIFT = 16
    private const val GREEN_SHIFT = 8
    private const val LAPLACIAN_CENTRE = 4

    /** Returns true when [score] must abstain as `image_quality`. */
    fun isTooBlurry(score: Double): Boolean = score < VARIANCE_LAPLACIAN_FLOOR

    /**
     * Variance of the interior 3x3 Laplacian of the PIL-style grayscale of
     * [argb] (row-major, [width] x [height]). Pure arithmetic, no Android
     * dependencies, so it is unit-testable against the server's numbers.
     * Returns 0.0 when the image is too small to have an interior.
     */
    fun laplacianVariance(argb: IntArray, width: Int, height: Int): Double {
        require(argb.size >= width * height) { "pixel buffer smaller than width*height" }
        if (width < 3 || height < 3) return 0.0
        val gray = IntArray(width * height)
        for (i in 0 until width * height) {
            val pixel = argb[i]
            val r = (pixel shr RED_SHIFT) and CHANNEL_MASK
            val g = (pixel shr GREEN_SHIFT) and CHANNEL_MASK
            val b = pixel and CHANNEL_MASK
            gray[i] = (r * LUMA_R + g * LUMA_G + b * LUMA_B + LUMA_ROUND) shr LUMA_SHIFT
        }
        var sum = 0.0
        var sumSq = 0.0
        val count = (width - 2) * (height - 2)
        for (y in 1 until height - 1) {
            val row = y * width
            for (x in 1 until width - 1) {
                val lap = gray[row - width + x] + gray[row + width + x] +
                    gray[row + x - 1] + gray[row + x + 1] -
                    LAPLACIAN_CENTRE * gray[row + x]
                sum += lap
                sumSq += lap.toDouble() * lap
            }
        }
        val mean = sum / count
        return sumSq / count - mean * mean
    }

    /**
     * Score the file at [imagePath] on the server's statistic. Decodes with a
     * power-of-two subsample that keeps the long edge at or above
     * [DOWNSCALE_MAX_SIDE], then filters down to exactly that long edge (the
     * server's bilinear downscale). On any failure returns
     * [Double.MAX_VALUE], i.e. assume sharp and let the server decide.
     */
    fun score(imagePath: String): Double {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(imagePath, bounds)
            val longEdge = maxOf(bounds.outWidth, bounds.outHeight)
            if (longEdge <= 0) return Double.MAX_VALUE
            var sample = 1
            while (longEdge / (sample * 2) >= DOWNSCALE_MAX_SIDE) sample *= 2
            val options = BitmapFactory.Options().apply { inSampleSize = sample }
            val decoded = BitmapFactory.decodeFile(imagePath, options) ?: return Double.MAX_VALUE
            val scaled = downscaleToLongEdge(decoded)
            val w = scaled.width
            val h = scaled.height
            val pixels = IntArray(w * h)
            scaled.getPixels(pixels, 0, w, 0, 0, w, h)
            if (scaled !== decoded) scaled.recycle()
            decoded.recycle()
            laplacianVariance(pixels, w, h)
        } catch (e: OutOfMemoryError) {
            Log.w(TAG, "score: out of memory", e)
            Double.MAX_VALUE
        } catch (e: Exception) {
            Log.w(TAG, "score failed", e)
            Double.MAX_VALUE
        }
    }

    private fun downscaleToLongEdge(bitmap: Bitmap): Bitmap {
        val longEdge = maxOf(bitmap.width, bitmap.height)
        if (longEdge <= DOWNSCALE_MAX_SIDE) return bitmap
        val scale = DOWNSCALE_MAX_SIDE / longEdge.toDouble()
        val w = maxOf(1, (bitmap.width * scale).toInt())
        val h = maxOf(1, (bitmap.height * scale).toInt())
        return Bitmap.createScaledBitmap(bitmap, w, h, true)
    }
}
