package com.plantdiseases.app.ui.result

import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View

/**
 * Custom view that draws a Grad-CAM-style heatmap overlay on top of a plant image.
 *
 * Accepts pixel coordinates from the server's detection.region and maps them
 * to view coordinates, accounting for the ImageView's centerCrop scaleType.
 */
class HeatmapOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var overlayAlpha = 0f
    private var showOverlay = false

    // Server-provided pixel coordinates in original image space
    private var regionX = 0
    private var regionY = 0
    private var regionW = 0
    private var regionH = 0
    // Original image dimensions (needed for centerCrop mapping)
    private var imgWidth = 0
    private var imgHeight = 0

    /**
     * Set the detection region using pixel coordinates from the server.
     * @param x      Left edge in original image pixels
     * @param y      Top edge in original image pixels
     * @param w      Region width in original image pixels
     * @param h      Region height in original image pixels
     * @param origW  Original image width
     * @param origH  Original image height
     */
    fun setDetectionRegion(x: Int, y: Int, w: Int, h: Int, origW: Int, origH: Int) {
        regionX = x
        regionY = y
        regionW = w
        regionH = h
        imgWidth = origW
        imgHeight = origH
        showOverlay = true
        invalidate()
    }

    /**
     * Legacy fallback: normalized coordinates (0-1) for center and radius.
     * Used when no real server coordinates are available.
     */
    fun setDetectionRegionNormalized(cx: Float = 0.5f, cy: Float = 0.5f, radius: Float = 0.35f) {
        val clampedCx = cx.coerceIn(0.1f, 0.9f)
        val clampedCy = cy.coerceIn(0.1f, 0.9f)
        val clampedR = radius.coerceIn(0.15f, 0.5f)
        // Convert to a synthetic pixel region (view-size based, imgWidth=0 signals normalized mode)
        regionX = 0
        regionY = 0
        regionW = 0
        regionH = 0
        imgWidth = 0
        imgHeight = 0
        showOverlay = true
        // Store normalized for drawing
        _normalizedCx = clampedCx
        _normalizedCy = clampedCy
        _normalizedRadius = clampedR
        invalidate()
    }

    private var _normalizedCx = 0.5f
    private var _normalizedCy = 0.5f
    private var _normalizedRadius = 0.35f

    fun animateIn() {
        animate()
            .alpha(1f)
            .setDuration(800)
            .start()
        val animator = android.animation.ValueAnimator.ofFloat(0f, 0.6f)
        animator.duration = 800
        animator.addUpdateListener {
            overlayAlpha = it.animatedValue as Float
            invalidate()
        }
        animator.start()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (!showOverlay || overlayAlpha == 0f) return

        val viewW = width.toFloat()
        val viewH = height.toFloat()

        val cx: Float
        val cy: Float
        val radius: Float

        if (imgWidth > 0 && imgHeight > 0) {
            // Real pixel coordinates — map through centerCrop transform
            val scale = maxOf(viewW / imgWidth, viewH / imgHeight)
            val scaledImgW = imgWidth * scale
            val scaledImgH = imgHeight * scale
            val offsetX = (scaledImgW - viewW) / 2f
            val offsetY = (scaledImgH - viewH) / 2f

            // Map region center from original image to view coordinates
            val regionCxOrig = regionX + regionW / 2f
            val regionCyOrig = regionY + regionH / 2f
            cx = regionCxOrig * scale - offsetX
            cy = regionCyOrig * scale - offsetY
            // Use the average of mapped width/height as radius
            radius = (regionW + regionH) / 2f * scale / 2f
        } else {
            // Normalized fallback
            cx = viewW * _normalizedCx
            cy = viewH * _normalizedCy
            radius = minOf(viewW, viewH) * _normalizedRadius
        }

        // Draw semi-transparent dark overlay on the whole image
        paint.color = Color.argb((40 * overlayAlpha).toInt(), 0, 0, 0)
        canvas.drawRect(0f, 0f, viewW, viewH, paint)

        // Draw heatmap gradient: red center -> yellow -> green -> transparent
        val gradient = RadialGradient(
            cx, cy, radius.coerceAtLeast(1f),
            intArrayOf(
                Color.argb((180 * overlayAlpha).toInt(), 255, 0, 0),
                Color.argb((140 * overlayAlpha).toInt(), 255, 165, 0),
                Color.argb((100 * overlayAlpha).toInt(), 255, 255, 0),
                Color.argb((60 * overlayAlpha).toInt(), 0, 255, 0),
                Color.TRANSPARENT
            ),
            floatArrayOf(0f, 0.3f, 0.5f, 0.7f, 1f),
            Shader.TileMode.CLAMP
        )
        paint.shader = gradient
        canvas.drawCircle(cx, cy, radius.coerceAtLeast(1f), paint)
        paint.shader = null

        // Draw a thin dashed border around the detection area
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 2f
        paint.color = Color.argb((200 * overlayAlpha).toInt(), 255, 80, 80)
        paint.pathEffect = DashPathEffect(floatArrayOf(10f, 5f), 0f)
        canvas.drawCircle(cx, cy, radius * 0.85f, paint)
        paint.pathEffect = null
        paint.style = Paint.Style.FILL
    }
}
