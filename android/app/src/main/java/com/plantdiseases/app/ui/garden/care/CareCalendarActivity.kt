package com.plantdiseases.app.ui.garden.care

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.local.gardenChanges
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.databinding.ActivityCareCalendarBinding
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import javax.inject.Inject

/**
 * Care calendar — the month grid + legend + "Today, N tasks" card, on its
 * own screen. This was the Garden tab's second pane until the Garden tab
 * became a single surface; it is now reached from the "Care calendar"
 * button at the top of that tab. Tapping a day opens [CareDayActivity].
 */
@AndroidEntryPoint
class CareCalendarActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCareCalendarBinding
    private var calendarView: CareCalendarView? = null
    private var monthCursor: Calendar = Calendar.getInstance()
    private var monthLoadJob: Job? = null

    @Inject
    lateinit var careRepository: CareRepository

    @Inject
    lateinit var database: AppDatabase

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityCareCalendarBinding.inflate(layoutInflater)
        setContentView(binding.root)

        savedInstanceState?.getLong(KEY_MONTH_MS, 0L)?.takeIf { it > 0L }?.let { ms ->
            monthCursor = Calendar.getInstance().apply { timeInMillis = ms }
        }

        binding.careCalendarScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.careCalendarAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.careCalendarScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.careCalendarToolbar.setNavigationOnClickListener { finish() }

        attachCalendarView()
        // The grid and the Today card follow the database, like the garden tab:
        // a task done in the day view, in the garden or from a reminder
        // notification repaints them, and coming back re-reads them (which also
        // moves "today" on after midnight). The first emission is the first
        // load. collectLatest drops a read a newer change made stale.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                database.gardenChanges().collectLatest {
                    renderMonth(monthStartOf(monthCursor))
                    renderToday()
                }
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putLong(KEY_MONTH_MS, monthCursor.timeInMillis)
    }

    private fun attachCalendarView() {
        if (calendarView != null) return
        val view = CareCalendarView(this).apply {
            onDaySelected = { epochDay -> openDay(CareQueue.dayStartMs(epochDay)) }
            onMonthChanged = { next ->
                monthCursor = next
                val month = monthStartOf(next)
                monthLoadJob?.cancel()
                monthLoadJob = lifecycleScope.launch { renderMonth(month) }
            }
        }
        binding.careCalendarContainer.addView(view)
        calendarView = view
        buildLegend()
    }

    /** [monthStart] is taken (via [monthStartOf]) when the load is requested,
     *  not when it runs, so a cursor moved meanwhile cannot change what is read. */
    private suspend fun renderMonth(monthStart: Calendar) {
        val monthEnd = (monthStart.clone() as Calendar).apply { add(Calendar.MONTH, 1) }
        val summary = careRepository.month(
            monthStart.timeInMillis,
            monthEnd.timeInMillis,
            System.currentTimeMillis(),
        )
        if (isFinishing || isDestroyed) return
        // Room queries are cancellable, but this explicit identity check
        // also protects the final UI write if an old result and a newer
        // chevron tap cross on the main-thread boundary.
        if (!sameMonth(monthStart, monthCursor)) return
        calendarView?.showMonth(monthStart, summary, selectedEpochDay = null)
    }

    private suspend fun renderToday() {
        val nowMs = System.currentTimeMillis()
        val todayStart = CareQueue.dayStartMs(CareQueue.epochDayOf(nowMs))
        val todayTasks = careRepository.today(nowMs).map { it.task }
        if (isFinishing || isDestroyed) return
        renderTodayCard(todayStart, todayTasks)
    }

    /** First day of [cursor]'s month at LOCAL midnight, taken through
     *  [CareQueue.dayStartMs] so the query window and the epoch-day buckets
     *  the grid keys on are built by the same code. */
    private fun monthStartOf(cursor: Calendar): Calendar =
        (cursor.clone() as Calendar).apply {
            set(Calendar.DAY_OF_MONTH, 1)
            timeInMillis = CareQueue.dayStartMs(CareQueue.epochDayOf(timeInMillis))
        }

    private fun sameMonth(first: Calendar, second: Calendar): Boolean =
        first.get(Calendar.YEAR) == second.get(Calendar.YEAR) &&
            first.get(Calendar.MONTH) == second.get(Calendar.MONTH)

    private fun renderTodayCard(todayStart: Long, tasks: List<CareQueue.CareTask>) {
        val pendingCount = tasks.count { it.status == CareQueue.CareTask.Status.PENDING }
        val dateLabel = SimpleDateFormat("d MMMM", Locale.getDefault()).format(Date(todayStart))
        binding.careCalendarTodayTitle.text = getString(R.string.calendar_today_prefix, dateLabel)
        binding.careCalendarTodaySubtitle.text = if (pendingCount == 0) {
            getString(R.string.calendar_all_caught_up)
        } else {
            resources.getQuantityString(R.plurals.care_tasks_count, pendingCount, pendingCount)
        }
        binding.careCalendarTodayCard.setOnClickListener {
            Haptics.tick(this)
            openDay(todayStart)
        }
    }

    private fun buildLegend() {
        binding.careCalendarLegend.removeAllViews()
        // Same soft-tint swatches the Lunar Calendar's own legend uses
        // (bg_lunar_day_bad/neutral/good) — the day cells fill with these
        // same drawables, so the key shows what's actually on the grid.
        listOf(
            R.drawable.bg_lunar_day_bad to R.string.calendar_legend_overdue,
            R.drawable.bg_lunar_day_neutral to R.string.calendar_legend_pending,
            R.drawable.bg_lunar_day_good to R.string.calendar_legend_done,
        ).forEach { (drawableRes, labelRes) ->
            binding.careCalendarLegend.addView(
                LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply {
                        marginStart = 10.dp()
                        marginEnd = 10.dp()
                    }
                    addView(
                        View(this@CareCalendarActivity).apply {
                            layoutParams = LinearLayout.LayoutParams(12.dp(), 12.dp())
                            setBackgroundResource(drawableRes)
                        }
                    )
                    addView(
                        TextView(this@CareCalendarActivity).apply {
                            text = this@CareCalendarActivity.getString(labelRes)
                            textSize = 12f
                            typeface = android.graphics.Typeface.DEFAULT_BOLD
                            setTextColor(this@CareCalendarActivity.getColor(R.color.on_surface))
                            setPadding(4.dp(), 0, 0, 0)
                        }
                    )
                }
            )
        }
    }

    private fun openDay(dayStartMs: Long) {
        startActivity(
            Intent(this, CareDayActivity::class.java)
                .putExtra(CareDayActivity.EXTRA_DAY_START_MS, dayStartMs)
        )
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()

    companion object {
        private const val KEY_MONTH_MS = "care_calendar_month_ms"

        fun intent(context: Context): Intent = Intent(context, CareCalendarActivity::class.java)
    }
}
