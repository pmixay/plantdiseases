package com.plantdiseases.app.ui.garden

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import com.plantdiseases.app.R

/**
 * Tiny line+dot chart for the 5-point severity sparkline returned by
 * `/api/progression?history=…`. Values are expected to live in [0, 1].
 *
 * Intentionally lightweight — no axis labels, no legend. The parent
 * card supplies the contextual trend chip + scan count. Empty input
 * draws a flat baseline so the "no data yet" state renders without
 * special casing.
 */
class SparklineView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    private var points: List<Float> = emptyList()
    private var trend: String = "baseline"

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(3f)
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val baselinePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(1f)
        color = context.getColor(R.color.on_surface_secondary)
        alpha = 60
    }
    private val linePath = Path()

    fun setData(points: List<Float>, trend: String) {
        this.points = points.map { it.coerceIn(0f, 1f) }
        this.trend = trend
        applyTrendColor()
        invalidate()
    }

    private fun applyTrendColor() {
        val color = when (trend) {
            "improving" -> context.getColor(R.color.healthy_green)
            "worsening" -> context.getColor(R.color.disease_red)
            "stable" -> context.getColor(R.color.neutral_blue)
            else -> context.getColor(R.color.on_surface_secondary)
        }
        linePaint.color = color
        dotPaint.color = color
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val padX = dp(8f)
        val padY = dp(10f)
        val w = (width - 2 * padX).coerceAtLeast(1f)
        val h = (height - 2 * padY).coerceAtLeast(1f)

        // Baseline at the bottom — anchors the eye even when the line
        // is flat at zero (healthy / no history).
        canvas.drawLine(padX, padY + h, padX + w, padY + h, baselinePaint)

        if (points.isEmpty()) return

        // Map (i, value) → (x, y). x evenly spaced; y inverted because
        // canvas y grows downward and we want "worse" to render higher.
        val n = points.size
        linePath.reset()
        val xs = FloatArray(n)
        val ys = FloatArray(n)
        for (i in 0 until n) {
            xs[i] = padX + w * (if (n == 1) 0.5f else i.toFloat() / (n - 1))
            ys[i] = padY + h * (1f - points[i])
        }
        linePath.moveTo(xs[0], ys[0])
        for (i in 1 until n) linePath.lineTo(xs[i], ys[i])
        canvas.drawPath(linePath, linePaint)

        // Endpoint dots with extra emphasis on the newest sample.
        val baseRadius = dp(3f)
        for (i in 0 until n) {
            val r = if (i == n - 1) baseRadius * 1.6f else baseRadius
            canvas.drawCircle(xs[i], ys[i], r, dotPaint)
        }
    }

    private fun dp(v: Float): Float = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        v,
        resources.displayMetrics,
    )
}
