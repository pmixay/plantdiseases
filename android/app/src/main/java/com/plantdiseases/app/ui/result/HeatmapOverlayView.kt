package com.plantdiseases.app.ui.result

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View

/**
 * Draws a Grad-CAM-style heatmap overlay on top of a plant image.
 * Accepts pixel coordinates from the server's detection.region and maps
 * them to view coordinates, accounting for centerCrop scaleType.
 */
class HeatmapOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val dimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f
        pathEffect = DashPathEffect(floatArrayOf(10f, 5f), 0f)
    }
    private var cachedGradient: RadialGradient? = null
    private var cachedRadius = 0f
    private var cachedCx = 0f
    private var cachedCy = 0f
    private var animator: ValueAnimator? = null
    private var overlayAlpha = 0f
    private var showOverlay = false

    private var regionX = 0
    private var regionY = 0
    private var regionW = 0
    private var regionH = 0
    private var imgWidth = 0
    private var imgHeight = 0

    fun setDetectionRegion(x: Int, y: Int, w: Int, h: Int, origW: Int, origH: Int) {
        regionX = x
        regionY = y
        regionW = w
        regionH = h
        imgWidth = origW
        imgHeight = origH
        showOverlay = true
        cachedGradient = null
        invalidate()
    }

    fun animateIn() {
        alpha = 1f
        animator?.cancel()
        animator = ValueAnimator.ofFloat(0f, 0.6f).apply {
            duration = 800
            addUpdateListener {
                overlayAlpha = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        cachedGradient = null
    }

    override fun onDetachedFromWindow() {
        animator?.cancel()
        animator = null
        cachedGradient = null
        super.onDetachedFromWindow()
    }

    private fun ensureGradient(cx: Float, cy: Float, radius: Float) {
        if (cachedGradient != null && cachedRadius == radius && cachedCx == cx && cachedCy == cy) return
        cachedGradient = RadialGradient(
            cx, cy, radius.coerceAtLeast(1f),
            intArrayOf(
                Color.argb(180, 255, 0, 0),
                Color.argb(140, 255, 165, 0),
                Color.argb(100, 255, 255, 0),
                Color.argb(60, 0, 255, 0),
                Color.TRANSPARENT
            ),
            floatArrayOf(0f, 0.3f, 0.5f, 0.7f, 1f),
            Shader.TileMode.CLAMP
        )
        cachedRadius = radius
        cachedCx = cx
        cachedCy = cy
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (!showOverlay || overlayAlpha == 0f || imgWidth <= 0 || imgHeight <= 0) return

        val viewW = width.toFloat()
        val viewH = height.toFloat()

        val scale = maxOf(viewW / imgWidth, viewH / imgHeight)
        val scaledImgW = imgWidth * scale
        val scaledImgH = imgHeight * scale
        val offsetX = (scaledImgW - viewW) / 2f
        val offsetY = (scaledImgH - viewH) / 2f

        val regionCxOrig = regionX + regionW / 2f
        val regionCyOrig = regionY + regionH / 2f
        val cx = regionCxOrig * scale - offsetX
        val cy = regionCyOrig * scale - offsetY
        val radius = (regionW + regionH) / 2f * scale / 2f
        val safeRadius = radius.coerceAtLeast(1f)

        dimPaint.color = Color.argb((40 * overlayAlpha).toInt(), 0, 0, 0)
        canvas.drawRect(0f, 0f, viewW, viewH, dimPaint)

        ensureGradient(cx, cy, safeRadius)
        fillPaint.shader = cachedGradient
        fillPaint.alpha = (overlayAlpha * 255).toInt()
        canvas.drawCircle(cx, cy, safeRadius, fillPaint)
        fillPaint.shader = null
        fillPaint.alpha = 255

        strokePaint.color = Color.argb((200 * overlayAlpha).toInt(), 255, 80, 80)
        canvas.drawCircle(cx, cy, safeRadius * 0.85f, strokePaint)
    }
}
