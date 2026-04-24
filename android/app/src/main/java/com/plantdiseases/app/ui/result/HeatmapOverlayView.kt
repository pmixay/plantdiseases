package com.plantdiseases.app.ui.result

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View

/**
 * Draws the YOLOv8 detector output on top of a plant image.
 *
 * For each detected box we draw a dashed outline and a soft radial
 * "spotlight". Diseased boxes are red/orange, healthy leaves are green.
 * The primary box (the one Stage 2 classified) is emphasised with a
 * thicker stroke.
 *
 * Coordinates arrive in original-image pixel space and are mapped to
 * view coordinates assuming the underlying ImageView uses centerCrop.
 */
class HeatmapOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    data class Box(
        val x: Int,
        val y: Int,
        val width: Int,
        val height: Int,
        val diseased: Boolean,
        val primary: Boolean,
    )

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val dimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f
        pathEffect = DashPathEffect(floatArrayOf(10f, 5f), 0f)
    }
    private var animator: ValueAnimator? = null
    private var overlayAlpha = 0f
    private var showOverlay = false

    private val boxes = mutableListOf<Box>()
    private var imgWidth = 0
    private var imgHeight = 0

    fun setBoxes(list: List<Box>, origW: Int, origH: Int) {
        boxes.clear()
        boxes.addAll(list)
        imgWidth = origW
        imgHeight = origH
        showOverlay = list.isNotEmpty()
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

    override fun onDetachedFromWindow() {
        animator?.cancel()
        animator = null
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (!showOverlay || overlayAlpha == 0f || imgWidth <= 0 || imgHeight <= 0 || boxes.isEmpty()) return

        val viewW = width.toFloat()
        val viewH = height.toFloat()
        val scale = maxOf(viewW / imgWidth, viewH / imgHeight)
        val scaledImgW = imgWidth * scale
        val scaledImgH = imgHeight * scale
        val offsetX = (scaledImgW - viewW) / 2f
        val offsetY = (scaledImgH - viewH) / 2f

        dimPaint.color = Color.argb((30 * overlayAlpha).toInt(), 0, 0, 0)
        canvas.drawRect(0f, 0f, viewW, viewH, dimPaint)

        boxes.forEach { box ->
            val left = box.x * scale - offsetX
            val top = box.y * scale - offsetY
            val right = (box.x + box.width) * scale - offsetX
            val bottom = (box.y + box.height) * scale - offsetY
            val cx = (left + right) / 2f
            val cy = (top + bottom) / 2f
            val radius = (maxOf(right - left, bottom - top) / 2f).coerceAtLeast(1f)

            val outerColor: Int
            val strokeColor: Int
            if (box.diseased) {
                outerColor = Color.argb(180, 255, 0, 0)
                strokeColor = Color.argb((220 * overlayAlpha).toInt(), 255, 80, 80)
            } else {
                outerColor = Color.argb(140, 0, 200, 80)
                strokeColor = Color.argb((200 * overlayAlpha).toInt(), 80, 220, 120)
            }

            val gradient = RadialGradient(
                cx, cy, radius,
                intArrayOf(
                    outerColor,
                    Color.argb(120, Color.red(outerColor), Color.green(outerColor), Color.blue(outerColor)),
                    Color.TRANSPARENT
                ),
                floatArrayOf(0f, 0.5f, 1f),
                Shader.TileMode.CLAMP
            )
            fillPaint.shader = gradient
            fillPaint.alpha = (overlayAlpha * 255).toInt()
            canvas.drawCircle(cx, cy, radius, fillPaint)
            fillPaint.shader = null
            fillPaint.alpha = 255

            strokePaint.color = strokeColor
            strokePaint.strokeWidth = if (box.primary) 5f else 2f
            val rect = RectF(left, top, right, bottom)
            canvas.drawRoundRect(rect, 8f, 8f, strokePaint)
        }
    }
}
