package com.plantdiseases.app.ui.garden.care

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.GridLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import com.plantdiseases.app.R
import com.plantdiseases.app.util.CareQueue
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/**
 * Month grid for the Garden tab's Care-calendar pane. A 7-column
 * [GridLayout] of 42 reusable cells rebound on every month change — cheap
 * enough at this scale that a Canvas-drawn widget would be needless
 * complexity (see ui/garden/SparklineView.kt for where a custom-drawn
 * View earns its place instead: a continuous line chart, not 42
 * independently-tappable cells).
 */
class CareCalendarView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : LinearLayout(context, attrs, defStyleAttr) {

    /** Fired with the epoch-day (CareQueue.epochDayOf convention) the
     *  user tapped. */
    var onDaySelected: ((Long) -> Unit)? = null

    /** Fired when the header chevrons move to a different month; the
     *  host reloads that month's [CareQueue.DayState] map and calls
     *  [showMonth] again. */
    var onMonthChanged: ((Calendar) -> Unit)? = null

    private val monthLabel: TextView
    private val grid: GridLayout
    private val cells = mutableListOf<DayCell>()
    private var monthCursor: Calendar = Calendar.getInstance()
    private var selectedEpochDay: Long? = null

    private data class DayCell(val container: FrameLayout, val number: TextView, val dots: LinearLayout)

    init {
        orientation = VERTICAL

        val header = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT)
        }
        val prev = ImageButton(context).apply {
            setImageResource(R.drawable.ic_chevron_down)
            rotation = 90f
            background = null
            setColorFilter(context.getColor(R.color.on_surface_secondary))
            layoutParams = LayoutParams(40.dp(), 40.dp())
            setOnClickListener { shiftMonth(-1) }
        }
        monthLabel = TextView(context).apply {
            textSize = 16f
            gravity = Gravity.CENTER
            setTextColor(context.getColor(R.color.on_surface))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            layoutParams = LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f)
        }
        val next = ImageButton(context).apply {
            setImageResource(R.drawable.ic_chevron_down)
            rotation = -90f
            background = null
            setColorFilter(context.getColor(R.color.on_surface_secondary))
            layoutParams = LayoutParams(40.dp(), 40.dp())
            setOnClickListener { shiftMonth(1) }
        }
        header.addView(prev)
        header.addView(monthLabel)
        header.addView(next)
        addView(header)

        val weekdayRow = LinearLayout(context).apply {
            orientation = HORIZONTAL
            layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
                topMargin = 4.dp()
            }
        }
        val symbols = java.text.DateFormatSymbols(Locale.getDefault()).shortWeekdays
        val firstDow = Calendar.getInstance().firstDayOfWeek
        for (i in 0 until 7) {
            val dayOfWeek = ((firstDow - 1 + i) % 7) + 1
            weekdayRow.addView(
                TextView(context).apply {
                    text = symbols[dayOfWeek].take(2).uppercase()
                    textSize = 11f
                    gravity = Gravity.CENTER
                    setTextColor(context.getColor(R.color.on_surface_secondary))
                    layoutParams = LinearLayout.LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f)
                }
            )
        }
        addView(weekdayRow)

        grid = GridLayout(context).apply {
            columnCount = 7
            rowCount = 6
            layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
                topMargin = 4.dp()
            }
        }
        for (i in 0 until 42) {
            val container = FrameLayout(context).apply {
                layoutParams = GridLayout.LayoutParams(
                    GridLayout.spec(i / 7, 1f),
                    GridLayout.spec(i % 7, 1f),
                ).apply { width = 0; height = 44.dp() }
                isClickable = true
                isFocusable = true
            }
            val number = TextView(context).apply {
                textSize = 13f
                gravity = Gravity.CENTER
                layoutParams = FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER)
            }
            val dots = LinearLayout(context).apply {
                orientation = HORIZONTAL
                gravity = Gravity.CENTER
                layoutParams = FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL,
                ).apply { bottomMargin = 4.dp() }
            }
            container.addView(number)
            container.addView(dots)
            grid.addView(container)
            cells += DayCell(container, number, dots)
        }
        addView(grid)
    }

    /** [monthStart] any date within the target month. [summary] maps
     *  epoch-day (CareQueue.epochDayOf) to that day's state — an absent
     *  key means nothing scheduled. */
    fun showMonth(monthStart: Calendar, summary: Map<Long, CareQueue.DayState>, selectedEpochDay: Long?) {
        monthCursor = monthStart.clone() as Calendar
        this.selectedEpochDay = selectedEpochDay
        monthLabel.text = SimpleDateFormat("LLLL yyyy", Locale.getDefault()).format(monthCursor.time)
            .replaceFirstChar { it.uppercase() }

        val gridStart = (monthCursor.clone() as Calendar).apply {
            set(Calendar.DAY_OF_MONTH, 1)
            val firstDow = firstDayOfWeek
            while (get(Calendar.DAY_OF_WEEK) != firstDow) add(Calendar.DAY_OF_MONTH, -1)
        }
        val targetMonth = monthCursor.get(Calendar.MONTH)
        val today = Calendar.getInstance()
        val cursor = gridStart.clone() as Calendar

        for (cell in cells) {
            val inMonth = cursor.get(Calendar.MONTH) == targetMonth
            val epochDay = CareQueue.epochDayOf(cursor.timeInMillis)
            val isToday = isSameDay(cursor, today)
            val isSelected = epochDay == selectedEpochDay

            cell.number.text = cursor.get(Calendar.DAY_OF_MONTH).toString()
            cell.number.alpha = if (inMonth) 1f else 0.3f
            cell.container.isEnabled = inMonth
            cell.container.background = when {
                isToday -> circleBackground(context.getColor(R.color.primary))
                isSelected -> ringBackground()
                else -> null
            }
            cell.number.setTextColor(
                context.getColor(if (isToday) R.color.on_hero else R.color.on_surface),
            )

            cell.dots.removeAllViews()
            val state = summary[epochDay]
            if (inMonth && state != null) {
                if (state.overdue > 0) cell.dots.addView(dot(R.color.disease_red))
                if (state.pending > 0) cell.dots.addView(dot(R.color.warning_amber))
                if (state.done > 0) cell.dots.addView(dot(R.color.healthy_green))
            }

            val dayEpoch = epochDay
            cell.container.setOnClickListener {
                if (inMonth) onDaySelected?.invoke(dayEpoch)
            }
            cursor.add(Calendar.DAY_OF_MONTH, 1)
        }
    }

    private fun shiftMonth(delta: Int) {
        val next = (monthCursor.clone() as Calendar).apply { add(Calendar.MONTH, delta) }
        onMonthChanged?.invoke(next)
    }

    private fun isSameDay(a: Calendar, b: Calendar): Boolean =
        a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR)

    private fun dot(colorRes: Int): View = View(context).apply {
        layoutParams = LinearLayout.LayoutParams(4.dp(), 4.dp()).apply { marginStart = 1.dp() }
        background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(context.getColor(colorRes))
        }
    }

    private fun circleBackground(color: Int) = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(color)
    }

    private fun ringBackground() = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(Color.TRANSPARENT)
        setStroke(2.dp(), context.getColor(R.color.primary))
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
