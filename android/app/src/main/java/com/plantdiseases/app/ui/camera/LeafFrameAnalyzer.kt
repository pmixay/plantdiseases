package com.plantdiseases.app.ui.camera

import android.graphics.Color
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy

/**
 * Real-time HSV-based leaf framing analyzer.
 *
 * Pulls every ~5th preview frame, downsamples to 160 px on the long
 * side, and reports two signals:
 *
 *  * a "frame quality" tier — `RED` (no leaf in sight), `AMBER` (a leaf
 *    is on screen but not centred / too sparse) or `GREEN` (centred,
 *    plenty of green pixels). Drives the colour of the on-screen
 *    rectangle.
 *  * a Laplacian-variance proxy on the green channel — used by the
 *    auto-capture trigger to skip blurry frames.
 *
 * The classifier on the server still owns the disease decision; this
 * is only a UX nudge for the user to compose a clean shot.
 */
class LeafFrameAnalyzer(
    private val onFrame: (FrameSignal) -> Unit,
) : ImageAnalysis.Analyzer {

    enum class Quality { RED, AMBER, GREEN }

    data class FrameSignal(
        val quality: Quality,
        val greenRatio: Float,
        val centroidOffset: Float, // 0..1, distance from frame centre
        val sharpness: Double,     // Laplacian variance proxy
    )

    private var frameIndex = 0

    override fun analyze(image: ImageProxy) {
        try {
            // Sample every ~5th frame to keep the main thread cool. The
            // preview itself is unaffected; ImageAnalysis runs on a
            // dedicated executor.
            if (frameIndex++ % 5 != 0) return
            val signal = computeSignal(image) ?: return
            onFrame(signal)
        } finally {
            image.close()
        }
    }

    private fun computeSignal(image: ImageProxy): FrameSignal? {
        // CameraX ImageAnalysis defaults to YUV_420_888. The Y plane is
        // a single-channel luminance; together with U/V we can build a
        // small RGB matrix. We only need a 160-px-wide thumbnail.
        val planes = image.planes
        if (planes.size < 3) return null
        val yPlane = planes[0]
        val uPlane = planes[1]
        val vPlane = planes[2]

        val rotation = image.imageInfo.rotationDegrees
        val width = image.width
        val height = image.height
        if (width <= 0 || height <= 0) return null

        val targetLong = 160
        val longest = maxOf(width, height)
        val step = (longest / targetLong).coerceAtLeast(1)

        val y = yPlane.buffer
        val u = uPlane.buffer
        val v = vPlane.buffer
        val yRow = yPlane.rowStride
        val uvRow = uPlane.rowStride
        val uvPix = uPlane.pixelStride

        // Reset positions so analyze() is reentrant on stale buffers.
        y.position(0); u.position(0); v.position(0)

        var greenCount = 0
        var sampled = 0
        var sumX = 0L
        var sumY = 0L
        // Build a compact green-channel grid for Laplacian variance.
        val gridW = (width / step).coerceAtLeast(8)
        val gridH = (height / step).coerceAtLeast(8)
        val gGrid = IntArray(gridW * gridH)
        var gi = 0

        var py = 0
        while (py < height && (py / step) < gridH) {
            var px = 0
            val yLineBase = py * yRow
            val uvLineBase = (py / 2) * uvRow
            while (px < width && (px / step) < gridW) {
                val yIdx = yLineBase + px
                val uvIdx = uvLineBase + (px / 2) * uvPix
                if (yIdx >= y.limit() || uvIdx >= u.limit() || uvIdx >= v.limit()) {
                    px += step
                    continue
                }
                val yv = (y.get(yIdx).toInt() and 0xFF)
                val uv = (u.get(uvIdx).toInt() and 0xFF) - 128
                val vv = (v.get(uvIdx).toInt() and 0xFF) - 128

                // ITU BT.601 → RGB.
                var r = yv + (1.402f * vv).toInt()
                var g = yv - (0.344136f * uv).toInt() - (0.714136f * vv).toInt()
                var b = yv + (1.772f * uv).toInt()
                r = r.coerceIn(0, 255)
                g = g.coerceIn(0, 255)
                b = b.coerceIn(0, 255)

                if (gi < gGrid.size) gGrid[gi++] = g

                val hsv = FloatArray(3)
                Color.RGBToHSV(r, g, b, hsv)
                val h = hsv[0]
                val s = hsv[1]
                val vVal = hsv[2]
                if (h in 35f..160f && s > 0.20f && vVal > 0.15f) {
                    greenCount++
                    sumX += px
                    sumY += py
                }
                sampled++
                px += step
            }
            py += step
        }

        if (sampled == 0) return null
        val greenRatio = greenCount.toFloat() / sampled

        val centreOffset: Float = if (greenCount > 0) {
            // Account for sensor rotation when projecting onto the
            // preview's coord system: a portrait phone delivers a
            // landscape-shaped frame and we want centroid offset to be
            // measured against the preview's geometry.
            val cx = sumX.toDouble() / greenCount
            val cy = sumY.toDouble() / greenCount
            val (centroidX, centroidY) = when (rotation) {
                90 -> cy to (width - cx)
                180 -> (width - cx) to (height - cy)
                270 -> (height - cy) to cx
                else -> cx to cy
            }
            val previewW = if (rotation % 180 == 0) width else height
            val previewH = if (rotation % 180 == 0) height else width
            val dx = (centroidX - previewW / 2.0) / (previewW / 2.0)
            val dy = (centroidY - previewH / 2.0) / (previewH / 2.0)
            kotlin.math.sqrt(dx * dx + dy * dy).toFloat()
                .coerceIn(0f, 1f)
        } else {
            1f
        }

        val sharpness = laplacianVariance(gGrid, gridW, gridH)

        // Tier thresholds:
        //  GREEN  — clearly framed, centred leaf area
        //  AMBER  — a leaf is visible but off-centre / sparse
        //  RED    — no plant signal worth analysing
        val quality = when {
            greenRatio < 0.06f -> Quality.RED
            greenRatio >= 0.18f && centreOffset < 0.45f -> Quality.GREEN
            else -> Quality.AMBER
        }

        return FrameSignal(
            quality = quality,
            greenRatio = greenRatio,
            centroidOffset = centreOffset,
            sharpness = sharpness,
        )
    }

    private fun laplacianVariance(grid: IntArray, w: Int, h: Int): Double {
        if (w < 3 || h < 3) return 0.0
        var sum = 0.0
        var sumSq = 0.0
        var n = 0
        for (yy in 1 until h - 1) {
            for (xx in 1 until w - 1) {
                val c = grid[yy * w + xx]
                val l = grid[(yy - 1) * w + xx] +
                    grid[(yy + 1) * w + xx] +
                    grid[yy * w + (xx - 1)] +
                    grid[yy * w + (xx + 1)] - 4 * c
                sum += l
                sumSq += l.toDouble() * l
                n++
            }
        }
        if (n == 0) return 0.0
        val mean = sum / n
        return (sumSq / n) - (mean * mean)
    }
}
