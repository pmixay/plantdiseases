package com.plantdiseases.app.ui.widget

import android.content.Context
import android.content.res.ColorStateList
import android.util.AttributeSet
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.GridLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.DrawableRes
import com.plantdiseases.app.R
import com.plantdiseases.app.util.LunarCalendar
import com.plantdiseases.app.util.PlantCategory
import com.plantdiseases.app.util.PlantCategoryClassifier
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The whole lunar-calendar section body: a month grid coloured by day rating
 * with an inline day-detail panel directly beneath it, and the next
 * favourable dates.
 *
 * Entirely offline — [LunarCalendar] derives everything from the date, so the
 * section renders instantly for every species and needs no server call. The
 * species (which the app receives as free text — see [PlantCategoryClassifier])
 * only tailors which days are rated favourable via [setSpecies]; it is never
 * required for the section to render.
 */
class LunarCalendarView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : FrameLayout(context, attrs, defStyle) {

    private val today: LocalDate = LocalDate.now()
    private val todayInRange: Boolean = LunarCalendar.isInRange(today)
    private val anchor: LocalDate = LunarCalendar.clampDate(today)
    private var shownMonth: YearMonth = YearMonth.from(anchor)
    private var selected: LocalDate = anchor
    private var category: PlantCategory = PlantCategory.FOLIAGE
    private var hasRendered = false

    /** One plant in [setAggregate]'s garden-wide view. */
    data class AggregateEntry(val label: String, val category: PlantCategory)

    /** Small host-saveable navigation snapshot. The view is normally created
     * programmatically (without a stable view id), so Android cannot restore
     * its browsed month/selected date automatically. */
    data class NavigationState(
        val shownMonth: YearMonth,
        val selectedDate: LocalDate,
    )

    /** Non-null in aggregate mode ([setAggregate]) — null means the
     *  single-species mode [setSpecies] already supported. */
    private var aggregateEntries: List<AggregateEntry>? = null

    /** Lets the host scroll the day panel into view when a tap selects a date
     *  that is off-screen (e.g. from an upcoming-day row). */
    var onRequestScrollTo: ((View) -> Unit)? = null

    private val monthLabel: TextView
    private val prevBtn: ImageView
    private val nextBtn: ImageView
    private val grid: GridLayout
    private val dayPanel: LinearLayout
    private val todayTitle: TextView
    private val categoryNote: TextView
    private val rangeNote: TextView
    private val moonView: MoonPhaseView
    private val phaseName: TextView
    private val signIcon: ImageView
    private val signView: TextView
    private val goodLabel: TextView
    private val goodTasks: LinearLayout
    private val avoidLabel: TextView
    private val avoidTasks: LinearLayout
    private val categoryBreakdown: LinearLayout
    private val legend: LinearLayout
    private val upcomingCard: View
    private val upcomingList: LinearLayout

    init {
        LayoutInflater.from(context).inflate(R.layout.view_lunar_calendar, this, true)

        monthLabel = findViewById(R.id.lunar_month_label)
        prevBtn = findViewById(R.id.lunar_prev)
        nextBtn = findViewById(R.id.lunar_next)
        grid = findViewById(R.id.lunar_grid)
        dayPanel = findViewById(R.id.lunar_day_panel)
        todayTitle = findViewById(R.id.lunar_today_title)
        categoryNote = findViewById(R.id.lunar_category_note)
        rangeNote = findViewById(R.id.lunar_range_note)
        moonView = findViewById(R.id.lunar_moon)
        phaseName = findViewById(R.id.lunar_phase_name)
        signIcon = findViewById(R.id.lunar_sign_icon)
        signView = findViewById(R.id.lunar_sign)
        goodLabel = findViewById(R.id.lunar_good_label)
        goodTasks = findViewById(R.id.lunar_good_tasks)
        avoidLabel = findViewById(R.id.lunar_avoid_label)
        avoidTasks = findViewById(R.id.lunar_avoid_tasks)
        categoryBreakdown = findViewById(R.id.lunar_category_breakdown)
        legend = findViewById(R.id.lunar_legend)
        upcomingCard = findViewById(R.id.lunar_upcoming_card)
        upcomingList = findViewById(R.id.lunar_upcoming_list)

        prevBtn.setOnClickListener {
            shownMonth = LunarCalendar.clampMonth(shownMonth.minusMonths(1))
            renderGrid()
            updateNavArrows()
        }
        nextBtn.setOnClickListener {
            shownMonth = LunarCalendar.clampMonth(shownMonth.plusMonths(1))
            renderGrid()
            updateNavArrows()
        }

        renderAll()
    }

    /** Free-text species in, category out — see [PlantCategoryClassifier]. Safe
     *  to call before or after the initial render; idempotent for the same
     *  resolved category. Switches back out of aggregate mode if it was on. */
    fun setSpecies(scientificName: String?, commonName: String?) {
        val next = PlantCategoryClassifier.classify(scientificName, commonName)
        if (next == category && aggregateEntries == null && hasRendered) return
        category = next
        aggregateEntries = null
        renderAll()
    }

    /** Garden-wide mode: one day rating per cell (the rating shared by the
     *  most plants — see [LunarCalendar.aggregateRating]), with the
     *  day-detail panel breaking the nuance back out by category. An empty
     *  list is a valid "no plants yet" state, not an error — the caller
     *  decides whether to show this view or an empty-state message instead. */
    fun setAggregate(entries: List<AggregateEntry>) {
        aggregateEntries = entries
        renderAll()
    }

    fun navigationState(): NavigationState = NavigationState(shownMonth, selected)

    fun restoreNavigationState(state: NavigationState) {
        shownMonth = LunarCalendar.clampMonth(state.shownMonth)
        selected = LunarCalendar.clampDate(state.selectedDate)
        renderAll()
    }

    private fun ratingFor(date: LocalDate): LunarCalendar.Rating =
        aggregateEntries?.let { LunarCalendar.aggregateRating(date, it.map { entry -> entry.category }) }
            ?: LunarCalendar.rating(date, category)

    private fun renderAll() {
        renderGrid()
        renderPanel(selected, animateMoon = false)
        renderLegend()
        renderUpcoming()
        updateNavArrows()
        hasRendered = true
    }

    // ── Grid ─────────────────────────────────────────────────────

    private fun renderGrid() {
        monthLabel.text = shownMonth.atDay(1)
            .format(DateTimeFormatter.ofPattern("LLLL yyyy", Locale.getDefault()))
            .replaceFirstChar { it.titlecase(Locale.getDefault()) }

        grid.removeAllViews()
        grid.columnCount = 7

        val first = shownMonth.atDay(1)
        val leading = first.dayOfWeek.value - 1
        val daysInMonth = shownMonth.lengthOfMonth()
        val bodyCells = leading + daysInMonth
        val trailing = (7 - bodyCells % 7) % 7
        val totalCells = bodyCells + trailing

        for (index in 0 until totalCells) {
            val date = first.plusDays((index - leading).toLong())
            val inMonth = date.month == shownMonth.month
            grid.addView(buildDayCell(date, inMonth))
        }
    }

    private fun buildDayCell(date: LocalDate, inMonth: Boolean): View {
        val cell = LayoutInflater.from(context).inflate(R.layout.item_lunar_day_cell, grid, false)
        cell.layoutParams = GridLayout.LayoutParams().apply {
            width = dp(36)
            height = dp(36)
            setMargins(dp(2), dp(2), dp(2), dp(2))
        }
        val day = cell.findViewById<TextView>(R.id.lunar_cell_day)
        day.text = date.dayOfMonth.toString()

        if (!inMonth || !LunarCalendar.isInRange(date)) {
            cell.findViewById<View>(R.id.lunar_cell_fill).background = null
            day.setTextColor(context.getColor(R.color.on_surface_secondary))
            cell.alpha = 0.35f
            cell.isClickable = false
            cell.isFocusable = false
            return cell
        }

        val rating = ratingFor(date)
        cell.findViewById<View>(R.id.lunar_cell_fill).setBackgroundResource(dayBackgroundRes(rating))
        day.setTextColor(context.getColor(dayForegroundRes(rating)))
        cell.findViewById<View>(R.id.lunar_cell_today_dot).visibility =
            if (todayInRange && date == today) View.VISIBLE else View.GONE
        cell.findViewById<View>(R.id.lunar_cell_ring).visibility =
            if (date == selected) View.VISIBLE else View.GONE
        cell.tag = date
        cell.contentDescription = accessibleDayLabel(date, rating)
        cell.setOnClickListener { selectDate(date) }
        return cell
    }

    /** Single entry point for selection — used by the grid AND the upcoming rows. */
    private fun selectDate(date: LocalDate, scrollToPanel: Boolean = false) {
        val target = LunarCalendar.clampDate(date)
        if (target == selected && hasRendered && !scrollToPanel) return
        selected = target
        val month = YearMonth.from(target)
        if (month != shownMonth) {
            shownMonth = month
            renderGrid()
            updateNavArrows()
        } else {
            updateSelectionRings()
        }
        renderPanel(target, animateMoon = true)
        if (scrollToPanel) onRequestScrollTo?.invoke(dayPanel)
    }

    /** O(42) ring toggle, no re-inflation — the ring visibly follows the tap. */
    private fun updateSelectionRings() {
        for (i in 0 until grid.childCount) {
            val cell = grid.getChildAt(i)
            val date = cell.tag as? LocalDate ?: continue
            cell.findViewById<View>(R.id.lunar_cell_ring).visibility =
                if (date == selected) View.VISIBLE else View.GONE
        }
    }

    private fun updateNavArrows() {
        val canPrev = shownMonth > LunarCalendar.MIN_MONTH
        val canNext = shownMonth < LunarCalendar.MAX_MONTH
        prevBtn.isEnabled = canPrev
        prevBtn.isClickable = canPrev
        prevBtn.alpha = if (canPrev) 1f else 0.28f
        nextBtn.isEnabled = canNext
        nextBtn.isClickable = canNext
        nextBtn.alpha = if (canNext) 1f else 0.28f
    }

    // ── Day panel ────────────────────────────────────────────────

    private fun renderPanel(date: LocalDate, animateMoon: Boolean) {
        // dayInfo's phase/zodiac/illumination are date-only; only its
        // rating/favourable/avoid depend on category, and those three are
        // ignored below in aggregate mode, so a stale `category` field there
        // is harmless.
        val info = LunarCalendar.dayInfo(date, category)
        todayTitle.text = if (date == today && todayInRange) {
            context.getString(R.string.lunar_today, formatDayMonth(date))
        } else {
            formatDayMonth(date)
        }
        rangeNote.visibility = if (todayInRange) View.GONE else View.VISIBLE
        if (!todayInRange) rangeNote.setText(R.string.lunar_out_of_range)

        moonView.setCycle(LunarCalendar.cyclePosition(date).toFloat(), animate = animateMoon)
        phaseName.setText(phaseStringRes(info.phase))
        signIcon.setImageResource(zodiacIconRes(info.zodiac))
        signView.text = context.getString(zodiacStringRes(info.zodiac))

        val entries = aggregateEntries
        if (entries != null) {
            // A single favourable/avoid task list doesn't generalise across
            // categories that can disagree on the same day — the breakdown
            // list below carries that nuance instead.
            categoryNote.visibility = View.GONE
            goodLabel.visibility = View.GONE
            goodTasks.visibility = View.GONE
            avoidLabel.visibility = View.GONE
            avoidTasks.visibility = View.GONE
            renderCategoryBreakdown(date, entries)
        } else {
            categoryNote.visibility = View.VISIBLE
            categoryNote.text = context.getString(R.string.lunar_category_note, context.getString(categoryStringRes(category)))
            goodLabel.visibility = View.VISIBLE
            avoidLabel.visibility = View.VISIBLE
            categoryBreakdown.visibility = View.GONE
            fillTaskRow(goodTasks, info.favourable, textColorRes = R.color.on_surface_secondary)
            fillTaskRow(avoidTasks, info.avoid, textColorRes = R.color.on_surface_secondary)
        }
    }

    /** One row per category present in the garden — its own rating for
     *  [date] plus which plants fall under it, so "good for your foliage,
     *  bad for your fruiting" stays visible instead of averaged into the
     *  single grid colour. Each row is two lines (category + rating word,
     *  then the plant names) so a long plant list wraps on its own line
     *  instead of shoving the category off the row, and a top gravity so
     *  the dot stays beside the first line rather than drifting to the
     *  middle of a wrapped row. */
    private fun renderCategoryBreakdown(date: LocalDate, entries: List<AggregateEntry>) {
        categoryBreakdown.removeAllViews()
        categoryBreakdown.visibility = View.VISIBLE
        categoryBreakdown.addView(
            TextView(context).apply {
                setText(R.string.lunar_breakdown_heading)
                setTextColor(context.getColor(R.color.primary))
                textSize = 13f
            },
        )
        entries.groupBy { it.category }.forEach { (cat, plantsInCategory) ->
            val rating = LunarCalendar.rating(date, cat)
            val row = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.TOP
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(10) }
            }
            row.addView(
                View(context).apply { setBackgroundResource(dayBackgroundRes(rating)) },
                LinearLayout.LayoutParams(dp(10), dp(10)).apply {
                    marginEnd = dp(8)
                    topMargin = dp(4)
                },
            )
            val textColumn = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
            textColumn.addView(
                TextView(context).apply {
                    text = context.getString(
                        R.string.lunar_breakdown_row,
                        context.getString(categoryStringRes(cat)),
                        context.getString(ratingWordRes(rating)),
                    )
                    setTextColor(context.getColor(R.color.on_surface))
                    textSize = 13f
                },
            )
            textColumn.addView(
                TextView(context).apply {
                    text = plantsInCategory.joinToString(", ") { it.label }
                    setTextColor(context.getColor(R.color.on_surface_secondary))
                    textSize = 12f
                },
            )
            row.addView(textColumn)
            categoryBreakdown.addView(row)
        }
    }

    private fun fillTaskRow(row: LinearLayout, tasks: List<LunarCalendar.Task>, textColorRes: Int) {
        row.removeAllViews()
        row.visibility = if (tasks.isEmpty()) View.GONE else View.VISIBLE
        tasks.forEach { task ->
            val tile = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
            }
            val icon = ImageView(context).apply {
                setImageResource(taskIconRes(task))
                imageTintList = ColorStateList.valueOf(context.getColor(R.color.primary))
            }
            tile.addView(icon, LinearLayout.LayoutParams(dp(24), dp(24)))
            val label = TextView(context).apply {
                setText(taskStringRes(task))
                setTextColor(context.getColor(textColorRes))
                textSize = 11f
                gravity = Gravity.CENTER
                setPadding(0, dp(4), 0, 0)
            }
            tile.addView(
                label,
                LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ),
            )
            row.addView(
                tile,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f),
            )
        }
    }

    // ── Legend + upcoming ────────────────────────────────────────

    private fun renderLegend() {
        legend.removeAllViews()
        legend.addView(legendRow(R.drawable.bg_lunar_day_good, R.string.lunar_legend_good))
        legend.addView(legendRow(R.drawable.bg_lunar_day_neutral, R.string.lunar_legend_neutral))
        legend.addView(legendRow(R.drawable.bg_lunar_day_bad, R.string.lunar_legend_bad))
    }

    private fun legendRow(dotRes: Int, labelRes: Int): View {
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(4), 0, dp(4))
        }
        val dot = View(context).apply { setBackgroundResource(dotRes) }
        row.addView(dot, LinearLayout.LayoutParams(dp(12), dp(12)).apply { marginEnd = dp(8) })
        val label = TextView(context).apply {
            setText(labelRes)
            setTextColor(context.getColor(R.color.on_surface_secondary))
            textSize = 13f
        }
        row.addView(label)
        return row
    }

    private fun renderUpcoming() {
        if (aggregateEntries != null) {
            upcomingCard.visibility = View.GONE
            return
        }
        upcomingCard.visibility = View.VISIBLE
        upcomingList.removeAllViews()
        LunarCalendar.upcomingFavourable(anchor, 4, category).forEach { info ->
            val row = LayoutInflater.from(context)
                .inflate(R.layout.item_lunar_favourable_day, upcomingList, false)
            row.findViewById<TextView>(R.id.lunar_row_date).text = formatDayMonth(info.date)
            row.findViewById<ImageView>(R.id.lunar_row_sign_icon).setImageResource(zodiacIconRes(info.zodiac))
            row.findViewById<TextView>(R.id.lunar_row_sign).text =
                context.getString(zodiacStringRes(info.zodiac))
            row.setOnClickListener { selectDate(info.date, scrollToPanel = true) }
            upcomingList.addView(row)
        }
    }

    // ── Formatting / resource lookups ───────────────────────────

    /**
     * "18 августа" / "18 August". Uses the `d MMMM` pattern (not
     * `Month.getDisplayName`) so Russian gets the grammatically correct
     * format-genitive month form — the same distinction `LLLL` vs `MMMM`
     * makes in [renderGrid]'s month header.
     */
    private fun formatDayMonth(date: LocalDate): String =
        date.format(DateTimeFormatter.ofPattern("d MMMM", Locale.getDefault()))

    private fun accessibleDayLabel(date: LocalDate, rating: LunarCalendar.Rating): String {
        val ratingLabel = context.getString(
            when (rating) {
                LunarCalendar.Rating.GOOD -> R.string.lunar_legend_good
                LunarCalendar.Rating.NEUTRAL -> R.string.lunar_legend_neutral
                LunarCalendar.Rating.BAD -> R.string.lunar_legend_bad
            },
        )
        return "${formatDayMonth(date)}, $ratingLabel"
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun dayBackgroundRes(rating: LunarCalendar.Rating): Int = when (rating) {
        LunarCalendar.Rating.GOOD -> R.drawable.bg_lunar_day_good
        LunarCalendar.Rating.NEUTRAL -> R.drawable.bg_lunar_day_neutral
        LunarCalendar.Rating.BAD -> R.drawable.bg_lunar_day_bad
    }

    private fun dayForegroundRes(rating: LunarCalendar.Rating): Int = when (rating) {
        LunarCalendar.Rating.GOOD -> R.color.lunar_good_fg
        LunarCalendar.Rating.NEUTRAL -> R.color.lunar_neutral_fg
        LunarCalendar.Rating.BAD -> R.color.lunar_bad_fg
    }

    /** Singular "favourable day" wording for a sentence — distinct from
     *  [R.string.lunar_legend_good] and its siblings, which are plural
     *  adjectives built for the legend ("favourable" agreeing with an
     *  implied "days"), not a standalone sentence. */
    private fun ratingWordRes(rating: LunarCalendar.Rating): Int = when (rating) {
        LunarCalendar.Rating.GOOD -> R.string.lunar_rating_good_day
        LunarCalendar.Rating.NEUTRAL -> R.string.lunar_rating_neutral_day
        LunarCalendar.Rating.BAD -> R.string.lunar_rating_bad_day
    }

    private fun phaseStringRes(phase: LunarCalendar.Phase): Int = when (phase) {
        LunarCalendar.Phase.NEW -> R.string.moon_new
        LunarCalendar.Phase.WAXING_CRESCENT -> R.string.moon_waxing_crescent
        LunarCalendar.Phase.FIRST_QUARTER -> R.string.moon_first_quarter
        LunarCalendar.Phase.WAXING_GIBBOUS -> R.string.moon_waxing_gibbous
        LunarCalendar.Phase.FULL -> R.string.moon_full
        LunarCalendar.Phase.WANING_GIBBOUS -> R.string.moon_waning_gibbous
        LunarCalendar.Phase.LAST_QUARTER -> R.string.moon_last_quarter
        LunarCalendar.Phase.WANING_CRESCENT -> R.string.moon_waning_crescent
    }

    private fun zodiacStringRes(zodiac: LunarCalendar.Zodiac): Int = when (zodiac) {
        LunarCalendar.Zodiac.ARIES -> R.string.zodiac_aries
        LunarCalendar.Zodiac.TAURUS -> R.string.zodiac_taurus
        LunarCalendar.Zodiac.GEMINI -> R.string.zodiac_gemini
        LunarCalendar.Zodiac.CANCER -> R.string.zodiac_cancer
        LunarCalendar.Zodiac.LEO -> R.string.zodiac_leo
        LunarCalendar.Zodiac.VIRGO -> R.string.zodiac_virgo
        LunarCalendar.Zodiac.LIBRA -> R.string.zodiac_libra
        LunarCalendar.Zodiac.SCORPIO -> R.string.zodiac_scorpio
        LunarCalendar.Zodiac.SAGITTARIUS -> R.string.zodiac_sagittarius
        LunarCalendar.Zodiac.CAPRICORN -> R.string.zodiac_capricorn
        LunarCalendar.Zodiac.AQUARIUS -> R.string.zodiac_aquarius
        LunarCalendar.Zodiac.PISCES -> R.string.zodiac_pisces
    }

    @DrawableRes
    private fun zodiacIconRes(zodiac: LunarCalendar.Zodiac): Int = when (zodiac) {
        LunarCalendar.Zodiac.ARIES -> R.drawable.ic_zodiac_aries
        LunarCalendar.Zodiac.TAURUS -> R.drawable.ic_zodiac_taurus
        LunarCalendar.Zodiac.GEMINI -> R.drawable.ic_zodiac_gemini
        LunarCalendar.Zodiac.CANCER -> R.drawable.ic_zodiac_cancer
        LunarCalendar.Zodiac.LEO -> R.drawable.ic_zodiac_leo
        LunarCalendar.Zodiac.VIRGO -> R.drawable.ic_zodiac_virgo
        LunarCalendar.Zodiac.LIBRA -> R.drawable.ic_zodiac_libra
        LunarCalendar.Zodiac.SCORPIO -> R.drawable.ic_zodiac_scorpio
        LunarCalendar.Zodiac.SAGITTARIUS -> R.drawable.ic_zodiac_sagittarius
        LunarCalendar.Zodiac.CAPRICORN -> R.drawable.ic_zodiac_capricorn
        LunarCalendar.Zodiac.AQUARIUS -> R.drawable.ic_zodiac_aquarius
        LunarCalendar.Zodiac.PISCES -> R.drawable.ic_zodiac_pisces
    }

    private fun taskStringRes(task: LunarCalendar.Task): Int = when (task) {
        LunarCalendar.Task.PLANTING -> R.string.lunar_task_planting
        LunarCalendar.Task.REPOTTING -> R.string.lunar_task_repotting
        LunarCalendar.Task.FEEDING -> R.string.lunar_task_feeding
        LunarCalendar.Task.WATERING -> R.string.lunar_task_watering
        LunarCalendar.Task.PRUNING -> R.string.lunar_task_pruning
    }

    private fun taskIconRes(task: LunarCalendar.Task): Int = when (task) {
        LunarCalendar.Task.PLANTING -> R.drawable.ic_garden
        LunarCalendar.Task.REPOTTING -> R.drawable.ic_repot
        LunarCalendar.Task.FEEDING -> R.drawable.ic_fertilize
        LunarCalendar.Task.WATERING -> R.drawable.ic_watering
        LunarCalendar.Task.PRUNING -> R.drawable.ic_prune
    }

    private fun categoryStringRes(category: PlantCategory): Int = when (category) {
        PlantCategory.FRUITING -> R.string.lunar_cat_fruiting
        PlantCategory.ROOT -> R.string.lunar_cat_root
        PlantCategory.LEAFY -> R.string.lunar_cat_leafy
        PlantCategory.FLOWERING -> R.string.lunar_cat_flowering
        PlantCategory.SUCCULENT -> R.string.lunar_cat_succulent
        PlantCategory.FOLIAGE -> R.string.lunar_cat_foliage
        PlantCategory.HERB -> R.string.lunar_cat_herb
    }
}
