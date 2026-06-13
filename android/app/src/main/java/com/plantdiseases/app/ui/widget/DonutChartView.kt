package com.plantdiseases.app.ui.widget

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.min

/**
 * Minimal donut chart drawn with [Canvas] — no chart dependency (see the
 * Stats redesign decision). Feed it [setSegments] with (value, colour)
 * pairs; it draws proportional arcs with rounded gaps and an optional
 * center label/sublabel. A zero total renders a single muted ring so the
 * widget still reads as a chart on an empty history.
 */
class DonutChartView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    data class Segment(val value: Float, val color: Int)

    private var segments: List<Segment> = emptyList()
    private var centerText: String = ""
    private var centerSubText: String = ""

    private val strokeWidthPx = dp(18f)
    private val gapDegrees = 3f

    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeWidth = strokeWidthPx
    }
    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = strokeWidthPx
        color = 0x22000000
    }
    private val titlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(22f)
        isFakeBoldText = true
    }
    private val subPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(12f)
    }

    private val oval = RectF()

    fun setSegments(segments: List<Segment>, centerText: String, centerSubText: String) {
        this.segments = segments.filter { it.value > 0f }
        this.centerText = centerText
        this.centerSubText = centerSubText
        invalidate()
    }

    /** Pull label colours from the host's resolved text colours. */
    fun setTextColors(primary: Int, secondary: Int) {
        titlePaint.color = primary
        subPaint.color = secondary
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val pad = strokeWidthPx / 2f + dp(2f)
        val size = min(width, height).toFloat()
        val left = (width - size) / 2f + pad
        val top = (height - size) / 2f + pad
        oval.set(left, top, left + size - 2 * pad, top + size - 2 * pad)

        val total = segments.sumOf { it.value.toDouble() }.toFloat()
        if (total <= 0f || segments.isEmpty()) {
            canvas.drawArc(oval, 0f, 360f, false, trackPaint)
        } else {
            var start = -90f
            val visible = segments.count { it.value > 0f }
            segments.forEach { seg ->
                val sweep = seg.value / total * 360f
                val gap = if (visible > 1) gapDegrees else 0f
                arcPaint.color = seg.color
                canvas.drawArc(oval, start + gap / 2f, sweep - gap, false, arcPaint)
                start += sweep
            }
        }

        val cx = oval.centerX()
        val cy = oval.centerY()
        if (centerText.isNotEmpty()) {
            canvas.drawText(centerText, cx, cy + dp(4f), titlePaint)
        }
        if (centerSubText.isNotEmpty()) {
            canvas.drawText(centerSubText, cx, cy + dp(22f), subPaint)
        }
    }

    private fun dp(v: Float): Float = v * resources.displayMetrics.density
}
