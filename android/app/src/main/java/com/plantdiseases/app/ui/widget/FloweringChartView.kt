package com.plantdiseases.app.ui.widget

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import com.plantdiseases.app.R
import java.util.Calendar
import kotlin.math.max

/**
 * Compact 12-month flowering calendar drawn without a chart dependency.
 *
 * Intensity values are 0 (none), 1 (some) and 2 (peak). The current month is
 * marked below its label and the status line is derived from the same series.
 * An all-zero series still renders the complete axes and month labels; its
 * status explains that indoor flowering is rare without making the graph
 * disappear.
 */
class FloweringChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    sealed interface BloomStatus {
        data object BloomsNow : BloomStatus

        data class NextBloom(val monthIndex: Int) : BloomStatus

        data object RarelyIndoors : BloomStatus
    }

    private var intensities: List<Int> = emptyList()
    private var currentMonth: Int = 0
    private var bloomStatus: BloomStatus? = null
    private var showStatusLine = true

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val emptyBarPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(10f)
    }
    private val axisLabelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.RIGHT
        textSize = dp(10f)
    }
    private val axisGuidePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(1f)
    }
    private val statusPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(14f)
        isFakeBoldText = true
    }
    private val markerPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()

    init {
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
        isFocusable = true
        refreshColors()
    }

    /**
     * Binds a validated server series. Returns false and clears the view for a
     * malformed series so callers can hide its surrounding card.
     */
    fun setFlowering(
        monthlyIntensity: List<Int>,
        monthIndex: Int = Calendar.getInstance().get(Calendar.MONTH),
    ): Boolean {
        val derived = deriveBloomStatus(monthlyIntensity, monthIndex)
        if (derived == null) {
            intensities = emptyList()
            bloomStatus = null
            contentDescription = null
            invalidate()
            return false
        }
        intensities = monthlyIntensity.toList()
        currentMonth = monthIndex
        bloomStatus = derived
        contentDescription = buildContentDescription(derived)
        invalidate()
        return true
    }

    fun statusText(): String? = bloomStatus?.let { statusText(context, it) }

    /** Hide the drawn status when a surrounding card already presents it. */
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val desiredWidth = dp(300f).toInt() + paddingLeft + paddingRight
        val desiredHeight = dp(184f).toInt() + paddingTop + paddingBottom
        setMeasuredDimension(
            resolveSize(desiredWidth, widthMeasureSpec),
            resolveSize(desiredHeight, heightMeasureSpec),
        )
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val status = bloomStatus ?: return
        refreshColors()

        val left = paddingLeft.toFloat()
        val right = width - paddingRight.toFloat()
        val top = paddingTop.toFloat()
        val bottom = height - paddingBottom.toFloat()
        val statusBaseline = bottom - dp(if (showStatusLine) 2f else 25f)

        val chartBottom = statusBaseline - dp(if (showStatusLine) 40f else 16f)
        val chartTop = top + dp(8f)
        val chartHeight = max(dp(1f), chartBottom - chartTop)
        val chartLeft = left + dp(intensityLabelGutterDp)
        val axisLabelX = chartLeft - dp(8f)
        val slot = (right - chartLeft) / MONTH_COUNT
        val barWidth = slot * 0.52f
        val months = resources.getStringArray(R.array.flowering_month_initials)

        // The scale is intentionally visible: bar heights are the three
        // server values, not a percentage or a health score.
        val yForIntensity = { intensity: Float -> chartBottom - chartHeight * intensity / 2f }
        listOf(0f, 1f, 2f).forEach { intensity ->
            val y = yForIntensity(intensity)
            canvas.drawLine(chartLeft, y, right, y, axisGuidePaint)
        }
        // Words, not 0/1/2 — the scale is meaningless without a legend, and
        // labelling it directly removes the need for one.
        drawAxisLabel(canvas, intensityLabels[2], axisLabelX, yForIntensity(2f))
        drawAxisLabel(canvas, intensityLabels[1], axisLabelX, yForIntensity(1f))
        drawAxisLabel(canvas, intensityLabels[0], axisLabelX, yForIntensity(0f))

        intensities.forEachIndexed { index, intensity ->
            val centerX = chartLeft + slot * index + slot / 2f
            val barHeight = when (intensity) {
                2 -> chartHeight
                1 -> chartHeight * 0.5f
                else -> dp(3f)
            }
            rect.set(
                centerX - barWidth / 2f,
                chartBottom - barHeight,
                centerX + barWidth / 2f,
                chartBottom,
            )
            val paint = if (intensity == 0) emptyBarPaint else barPaint
            val radius = minOf(dp(5f), barWidth / 2f)
            canvas.drawRoundRect(rect, radius, radius, paint)
            canvas.drawText(months[index], centerX, chartBottom + dp(17f), labelPaint)
            if (index == currentMonth) {
                canvas.drawCircle(centerX, chartBottom + dp(25f), dp(2.5f), markerPaint)
            }
        }
        if (showStatusLine) {
            canvas.drawText(
                statusText(context, status),
                (left + right) / 2f,
                statusBaseline,
                statusPaint,
            )
        }
    }

    /** Localized "none / occasional / peak", indexed by intensity 0..2. */
    private val intensityLabels: List<String> by lazy {
        listOf(
            context.getString(R.string.flowering_level_none),
            context.getString(R.string.flowering_level_occasional),
            context.getString(R.string.flowering_level_peak),
        )
    }

    /** Widest label plus breathing room, so the plot never overlaps the axis. */
    private val intensityLabelGutterDp: Float by lazy {
        val widest = intensityLabels.maxOf { axisLabelPaint.measureText(it) }
        widest / resources.displayMetrics.density + 10f
    }

    private fun drawAxisLabel(canvas: Canvas, label: String, x: Float, centerY: Float) {
        val metrics = axisLabelPaint.fontMetrics
        val baseline = centerY - (metrics.ascent + metrics.descent) / 2f
        canvas.drawText(label, x, baseline, axisLabelPaint)
    }

    private fun refreshColors() {
        // The flowering chart lives on a normal glass card, not a care-plan
        // hero. Keep the signal green but translucent so the graph feels
        // lighter and the card's surface remains visible through the bars.
        barPaint.color = context.getColor(R.color.primary)
        barPaint.alpha = 148
        emptyBarPaint.color = context.getColor(R.color.primary)
        emptyBarPaint.alpha = 52
        labelPaint.color = context.getColor(R.color.on_surface)
        labelPaint.alpha = 210
        axisLabelPaint.color = context.getColor(R.color.on_surface)
        axisLabelPaint.alpha = 230
        axisGuidePaint.color = context.getColor(R.color.divider)
        axisGuidePaint.alpha = 150
        statusPaint.color = context.getColor(R.color.on_surface)
        statusPaint.alpha = 255
        markerPaint.color = context.getColor(R.color.primary)
        markerPaint.alpha = 210
    }

    private fun buildContentDescription(status: BloomStatus): String {
        // Nominative names: the list reads "January: peak", not the
        // prepositional forms the Russian "Next bloom in %s" status needs.
        val monthNames = resources.getStringArray(R.array.flowering_month_names_nominative)
        val pattern = intensities.mapIndexed { index, intensity ->
            context.getString(
                R.string.flowering_accessibility_month_intensity,
                monthNames[index],
                intensityLabels[intensity.coerceIn(0, 2)],
            )
        }.joinToString()
        return context.getString(
            R.string.flowering_chart_content_description,
            statusText(context, status),
            pattern,
        )
    }

    private fun dp(value: Float): Float = value * resources.displayMetrics.density

    companion object {
        private const val MONTH_COUNT = 12

        /**
         * Pure date-derived state used by the Canvas and unit tests.
         * The next bloom search wraps December → January.
         */
        fun deriveBloomStatus(
            monthlyIntensity: List<Int>,
            currentMonth: Int,
        ): BloomStatus? {
            if (
                monthlyIntensity.size != MONTH_COUNT ||
                monthlyIntensity.any { it !in 0..2 } ||
                currentMonth !in 0 until MONTH_COUNT
            ) {
                return null
            }
            if (monthlyIntensity.all { it == 0 }) return BloomStatus.RarelyIndoors
            if (monthlyIntensity[currentMonth] > 0) return BloomStatus.BloomsNow

            for (offset in 1 until MONTH_COUNT) {
                val month = (currentMonth + offset) % MONTH_COUNT
                if (monthlyIntensity[month] > 0) return BloomStatus.NextBloom(month)
            }
            return BloomStatus.RarelyIndoors
        }

        fun statusText(context: Context, status: BloomStatus): String =
            when (status) {
                BloomStatus.BloomsNow -> context.getString(R.string.flowering_blooms_now)
                is BloomStatus.NextBloom -> {
                    // Prepositional in Russian — this array exists to be
                    // substituted into flowering_next_bloom.
                    val month = context.resources
                        .getStringArray(R.array.flowering_month_names)[status.monthIndex]
                    context.getString(R.string.flowering_next_bloom, month)
                }
                BloomStatus.RarelyIndoors ->
                    context.getString(R.string.flowering_rarely_indoors)
            }
    }
}
