package com.plantdiseases.app.ui.camera

import android.graphics.ImageFormat
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy

/**
 * Real-time scan-quality coach.
 *
 * Reads the luma (Y) plane of each analysed frame and grades exposure and
 * focus entirely on-device — the focus signal is a variance-of-Laplacian,
 * the same family of metric the server's blur gate uses to reject unfocused
 * uploads. Surfacing it live means the user is nudged toward a frame the
 * pipeline will actually accept instead of burning a network round-trip on a
 * shot that comes back as ``blurry_image`` / ``not_a_plant``.
 *
 * Cheap by construction: the Y plane is decimated to a coarse grid before any
 * arithmetic, frames are throttled to [MIN_INTERVAL_MS], and the analyzer is
 * bound with ``STRATEGY_KEEP_ONLY_LATEST`` so a slow grade can never back up
 * the camera. [onResult] is invoked on the analyzer's executor thread — the
 * caller is responsible for hopping to the main thread before touching views.
 */
class ScanQualityAnalyzer(
    private val onResult: (ScanQuality) -> Unit,
) : ImageAnalysis.Analyzer {

    enum class ScanQuality { TOO_DARK, TOO_BRIGHT, BLURRY, GOOD }

    private var lastAnalysisMs = 0L
    private var emitted: ScanQuality? = null
    private var pending: ScanQuality? = null
    private var pendingCount = 0

    override fun analyze(image: ImageProxy) {
        try {
            val now = System.currentTimeMillis()
            if (now - lastAnalysisMs < MIN_INTERVAL_MS) return
            lastAnalysisMs = now
            if (image.format != ImageFormat.YUV_420_888 || image.planes.isEmpty()) return

            val quality = grade(image)

            // Hysteresis — a new verdict has to hold for STABLE_FRAMES
            // consecutive reads before it's surfaced, so the hint doesn't
            // flicker on a single shaky frame.
            if (quality == emitted) {
                pending = null
                pendingCount = 0
                return
            }
            if (quality == pending) {
                pendingCount++
            } else {
                pending = quality
                pendingCount = 1
            }
            if (pendingCount >= STABLE_FRAMES) {
                emitted = quality
                pending = null
                pendingCount = 0
                onResult(quality)
            }
        } catch (_: Throwable) {
            // A quality hint is strictly advisory — never let a decode quirk
            // on some OEM's YUV layout crash the camera screen.
        } finally {
            image.close()
        }
    }

    private fun grade(image: ImageProxy): ScanQuality {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val limit = buffer.limit()
        if (limit <= 0) return ScanQuality.GOOD
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride.coerceAtLeast(1)
        val width = image.width
        val height = image.height
        if (width <= 0 || height <= 0) return ScanQuality.GOOD

        // Decimate to a grid of ~GRID_DIM samples per axis.
        val stepX = (width / GRID_DIM).coerceAtLeast(1)
        val stepY = (height / GRID_DIM).coerceAtLeast(1)
        val cols = (width + stepX - 1) / stepX
        val rows = (height + stepY - 1) / stepY
        if (cols < 3 || rows < 3) return ScanQuality.GOOD
        val luma = IntArray(cols * rows)

        var sum = 0L
        var idx = 0
        var y = 0
        while (y < height && idx < luma.size) {
            val rowBase = y * rowStride
            var x = 0
            while (x < width && idx < luma.size) {
                // Clamp to the buffer's last byte: some OEMs size the Y plane
                // to rowStride*(height-1)+width, so the final row can fall a
                // few bytes short. Clamping keeps the grid aligned.
                val pos = (rowBase + x * pixelStride).coerceIn(0, limit - 1)
                val v = buffer.get(pos).toInt() and 0xFF
                luma[idx++] = v
                sum += v
                x += stepX
            }
            y += stepY
        }
        val count = idx
        if (count == 0) return ScanQuality.GOOD
        val mean = sum.toDouble() / count

        if (mean < DARK_LUMA) return ScanQuality.TOO_DARK
        if (mean > BRIGHT_LUMA) return ScanQuality.TOO_BRIGHT

        // Variance of a 4-neighbour Laplacian over the grid. Low variance =
        // few sharp edges = a soft / out-of-focus frame.
        var lapSum = 0.0
        var lapSqSum = 0.0
        var n = 0
        for (r in 1 until rows - 1) {
            val base = r * cols
            for (c in 1 until cols - 1) {
                val i = base + c
                if (i - cols < 0 || i + cols >= count) continue
                val lap = 4 * luma[i] - luma[i - 1] - luma[i + 1] - luma[i - cols] - luma[i + cols]
                lapSum += lap
                lapSqSum += lap.toDouble() * lap
                n++
            }
        }
        if (n == 0) return ScanQuality.GOOD
        val lapMean = lapSum / n
        val variance = (lapSqSum / n) - lapMean * lapMean
        return if (variance < BLUR_VARIANCE) ScanQuality.BLURRY else ScanQuality.GOOD
    }

    companion object {
        private const val MIN_INTERVAL_MS = 250L
        private const val STABLE_FRAMES = 2
        /** Grid resolution per axis the Y plane is decimated to. */
        private const val GRID_DIM = 80
        private const val DARK_LUMA = 45.0
        private const val BRIGHT_LUMA = 235.0
        // Threshold on the decimated-grid Laplacian variance (NOT full-res),
        // deliberately set low so the coach errs toward "GOOD" rather than
        // nagging on fine-textured leaves. Raise to demand sharper frames.
        private const val BLUR_VARIANCE = 45.0
    }
}
