package com.plantdiseases.app.ui.result

import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View

/**
 * Custom view that draws a Grad-CAM-style heatmap overlay on top of a plant image.
 * Simulates a detection region visualization using radial gradient.
 */
class HeatmapOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var centerXRatio = 0.5f
    private var centerYRatio = 0.5f
    private var radiusRatio = 0.35f
    private var overlayAlpha = 0f
    private var showOverlay = false

    /**
     * Set the detection region from server's detection.region data.
     * Expected format: normalized coordinates (0-1) for center and radius.
     */
    fun setDetectionRegion(cx: Float = 0.5f, cy: Float = 0.5f, radius: Float = 0.35f) {
        centerXRatio = cx.coerceIn(0.1f, 0.9f)
        centerYRatio = cy.coerceIn(0.1f, 0.9f)
        radiusRatio = radius.coerceIn(0.15f, 0.5f)
        showOverlay = true
        invalidate()
    }

    fun animateIn() {
        animate()
            .alpha(1f)
            .setDuration(800)
            .start()
        // Animate overlay alpha
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

        val w = width.toFloat()
        val h = height.toFloat()
        val cx = w * centerXRatio
        val cy = h * centerYRatio
        val radius = minOf(w, h) * radiusRatio

        // Draw semi-transparent dark overlay on the whole image
        paint.color = Color.argb((40 * overlayAlpha).toInt(), 0, 0, 0)
        canvas.drawRect(0f, 0f, w, h, paint)

        // Draw heatmap gradient: red center -> yellow -> green -> transparent
        val gradient = RadialGradient(
            cx, cy, radius,
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
        canvas.drawCircle(cx, cy, radius, paint)
        paint.shader = null

        // Draw a thin pulsing border around the detection area
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 2f
        paint.color = Color.argb((200 * overlayAlpha).toInt(), 255, 80, 80)
        paint.pathEffect = DashPathEffect(floatArrayOf(10f, 5f), 0f)
        canvas.drawCircle(cx, cy, radius * 0.85f, paint)
        paint.pathEffect = null
        paint.style = Paint.Style.FILL
    }
}
