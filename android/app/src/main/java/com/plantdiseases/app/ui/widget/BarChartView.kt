package com.plantdiseases.app.ui.widget

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.max

/**
 * Minimal vertical bar chart drawn with [Canvas] — no chart dependency
 * (see the Stats redesign decision). Used for "scans over the last N days":
 * each entry is a (label, value) pair; bars scale to the tallest value and
 * the label sits under each bar. An all-zero series still draws the axis
 * baseline and labels so the chart reads as empty rather than broken.
 */
class BarChartView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    data class Bar(val label: String, val value: Int)

    private var bars: List<Bar> = emptyList()
    private var barColor: Int = 0xFF16A34A.toInt()

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(11f)
    }
    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(11f)
        isFakeBoldText = true
    }
    private val rect = RectF()

    fun setBars(bars: List<Bar>, barColor: Int, labelColor: Int, valueColor: Int) {
        this.bars = bars
        this.barColor = barColor
        labelPaint.color = labelColor
        valuePaint.color = valueColor
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (bars.isEmpty()) return
        val labelH = dp(18f)
        val valueH = dp(16f)
        val chartTop = valueH
        val chartBottom = height - labelH
        val chartH = max(1f, chartBottom - chartTop)
        val slot = width.toFloat() / bars.size
        val barW = slot * 0.5f
        val maxVal = max(1, bars.maxOf { it.value })

        bars.forEachIndexed { i, bar ->
            val cx = slot * i + slot / 2f
            val h = if (bar.value <= 0) dp(2f) else chartH * (bar.value / maxVal.toFloat())
            val topY = chartBottom - h
            rect.set(cx - barW / 2f, topY, cx + barW / 2f, chartBottom)
            barPaint.color = if (bar.value <= 0) 0x22000000 else barColor
            val r = dp(4f)
            canvas.drawRoundRect(rect, r, r, barPaint)
            if (bar.value > 0) {
                canvas.drawText(bar.value.toString(), cx, topY - dp(4f), valuePaint)
            }
            canvas.drawText(bar.label, cx, height - dp(4f), labelPaint)
        }
    }

    private fun dp(v: Float): Float = v * resources.displayMetrics.density
}
