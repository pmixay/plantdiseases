package com.plantdiseases.app.ui.garden.care

import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
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
 *
 * Cells reuse item_lunar_day_cell.xml and its bg_lunar_day_good/neutral/bad
 * fill drawables — the same fill+ring+day+today-dot layering and the same
 * soft green/amber/red tints the Lunar Calendar pane uses, just mapped to
 * done/pending/overdue instead of good/neutral/bad ratings. The "lunar_"
 * naming is a pre-existing artefact of those resources having shipped for
 * the lunar pane first; nothing about the layout or drawables is actually
 * lunar-specific.
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

    private data class DayCell(
        val container: View,
        val fill: View,
        val day: TextView,
        val todayDot: View,
        val ring: View,
    )

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
                    textSize = 12f
                    typeface = android.graphics.Typeface.DEFAULT_BOLD
                    gravity = Gravity.CENTER
                    setTextColor(context.getColor(R.color.on_surface))
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
            val container = LayoutInflater.from(context).inflate(R.layout.item_lunar_day_cell, grid, false)
            container.layoutParams = GridLayout.LayoutParams(
                GridLayout.spec(i / 7, 1f),
                GridLayout.spec(i % 7, 1f),
            ).apply {
                width = 0
                height = 44.dp()
                setMargins(2.dp(), 2.dp(), 2.dp(), 2.dp())
            }
            grid.addView(container)
            cells += DayCell(
                container = container,
                fill = container.findViewById(R.id.lunar_cell_fill),
                day = container.findViewById(R.id.lunar_cell_day),
                todayDot = container.findViewById(R.id.lunar_cell_today_dot),
                ring = container.findViewById(R.id.lunar_cell_ring),
            )
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

            // Days outside the month stay blank rather than faded: the design
            // system has no dimmed (grey) text.
            cell.day.text = if (inMonth) cursor.get(Calendar.DAY_OF_MONTH).toString() else ""
            cell.container.isEnabled = inMonth
            cell.ring.visibility = if (inMonth && isSelected) View.VISIBLE else View.GONE
            cell.todayDot.visibility = if (inMonth && isToday) View.VISIBLE else View.GONE

            // One status per cell, worst-first — a day overdue on even one
            // task reads as overdue regardless of what else is scheduled.
            val state = summary[epochDay]
            val status = when {
                !inMonth || state == null -> null
                state.overdue > 0 -> Status.OVERDUE
                state.pending > 0 -> Status.PENDING
                state.done > 0 -> Status.DONE
                else -> null
            }
            cell.fill.setBackgroundResource(status?.bgRes ?: 0)
            cell.day.setTextColor(context.getColor(status?.fgRes ?: R.color.on_surface))

            val dayEpoch = epochDay
            cell.container.setOnClickListener {
                if (inMonth) onDaySelected?.invoke(dayEpoch)
            }
            cursor.add(Calendar.DAY_OF_MONTH, 1)
        }
    }

    /** Same soft-tint pairing the Lunar Calendar uses for good/neutral/bad,
     *  reused here for done/pending/overdue. */
    private enum class Status(val bgRes: Int, val fgRes: Int) {
        OVERDUE(R.drawable.bg_lunar_day_bad, R.color.lunar_bad_fg),
        PENDING(R.drawable.bg_lunar_day_neutral, R.color.lunar_neutral_fg),
        DONE(R.drawable.bg_lunar_day_good, R.color.lunar_good_fg),
    }

    private fun shiftMonth(delta: Int) {
        val next = (monthCursor.clone() as Calendar).apply { add(Calendar.MONTH, delta) }
        // Advance the local cursor immediately. Waiting for the host's async
        // repository load to call showMonth() meant two quick chevron taps
        // both cloned the same month and only moved one month in total.
        monthCursor = next
        onMonthChanged?.invoke(next.clone() as Calendar)
    }

    private fun isSameDay(a: Calendar, b: Calendar): Boolean =
        a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR)

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
