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
 * An all-zero series is intentionally rendered as a dedicated "rarely flowers
 * indoors" panel rather than twelve misleading minimum-height bars.
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

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val emptyBarPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(10f)
    }
    private val statusPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = dp(14f)
        isFakeBoldText = true
    }
    private val markerPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rarePanelPaint = Paint(Paint.ANTI_ALIAS_FLAG)
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
        val statusBaseline = bottom - dp(2f)

        if (status is BloomStatus.RarelyIndoors) {
            drawRareState(canvas, left, right, top, statusBaseline)
            return
        }

        val chartBottom = statusBaseline - dp(40f)
        val chartTop = top + dp(8f)
        val chartHeight = max(dp(1f), chartBottom - chartTop)
        val slot = (right - left) / MONTH_COUNT
        val barWidth = slot * 0.52f
        val months = resources.getStringArray(R.array.flowering_month_initials)

        intensities.forEachIndexed { index, intensity ->
            val centerX = left + slot * index + slot / 2f
            val barHeight = when (intensity) {
                2 -> chartHeight
                1 -> chartHeight * 0.55f
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
        canvas.drawText(statusText(context, status), (left + right) / 2f, statusBaseline, statusPaint)
    }

    private fun drawRareState(
        canvas: Canvas,
        left: Float,
        right: Float,
        top: Float,
        statusBaseline: Float,
    ) {
        rect.set(left, top + dp(8f), right, statusBaseline - dp(20f))
        canvas.drawRoundRect(rect, dp(16f), dp(16f), rarePanelPaint)

        val centerX = (left + right) / 2f
        val centerY = rect.centerY() - dp(8f)
        canvas.drawCircle(centerX, centerY, dp(12f), markerPaint)
        canvas.drawCircle(centerX - dp(15f), centerY + dp(9f), dp(6f), emptyBarPaint)
        canvas.drawCircle(centerX + dp(15f), centerY + dp(9f), dp(6f), emptyBarPaint)
        canvas.drawText(
            statusText(context, BloomStatus.RarelyIndoors),
            centerX,
            statusBaseline,
            statusPaint,
        )
    }

    private fun refreshColors() {
        barPaint.color = context.getColor(R.color.on_hero)
        emptyBarPaint.color = context.getColor(R.color.hero_stat_stroke)
        labelPaint.color = context.getColor(R.color.on_hero_secondary)
        statusPaint.color = context.getColor(R.color.on_hero)
        markerPaint.color = context.getColor(R.color.primary_light)
        rarePanelPaint.color = context.getColor(R.color.hero_stat_bg)
    }

    private fun buildContentDescription(status: BloomStatus): String {
        val monthNames = resources.getStringArray(R.array.flowering_month_names)
        val pattern = intensities.mapIndexed { index, intensity ->
            context.getString(
                R.string.flowering_accessibility_month_intensity,
                monthNames[index],
                intensity,
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
                    val month = context.resources
                        .getStringArray(R.array.flowering_month_names)[status.monthIndex]
                    context.getString(R.string.flowering_next_bloom, month)
                }
                BloomStatus.RarelyIndoors ->
                    context.getString(R.string.flowering_rarely_indoors)
            }
    }
}
