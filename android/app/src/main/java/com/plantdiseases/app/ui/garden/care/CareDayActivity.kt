package com.plantdiseases.app.ui.garden.care

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.format.DateFormat
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.StringRes
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.google.android.material.snackbar.Snackbar
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.databinding.ActivityCareDayBinding
import com.plantdiseases.app.databinding.ItemCareTaskBinding
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.util.ReminderPresets
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject

/**
 * Day view — reached from the calendar's "Today, N tasks" card or a
 * tapped day cell. Shows overdue-from-earlier-days tasks in their own
 * header (never silently invisible), pending tasks, and a collapsed
 * "Show completed" section.
 */
@AndroidEntryPoint
class CareDayActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCareDayBinding
    private var dayStartMs: Long = 0L
    private var doneExpanded = false
    private var loadJob: Job? = null

    @Inject
    lateinit var careRepository: CareRepository

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        dayStartMs = intent.getLongExtra(EXTRA_DAY_START_MS, todayStartMs())
        doneExpanded = savedInstanceState?.getBoolean(KEY_DONE_EXPANDED, false) ?: false
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityCareDayBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.careDayScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.careDayAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.careDayScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.careDayToolbar.setNavigationOnClickListener { finish() }
        binding.careDayToggleDone.setOnClickListener {
            doneExpanded = !doneExpanded
            binding.careDayDoneContainer.visibility = if (doneExpanded) View.VISIBLE else View.GONE
            updateToggleLabel(lastDoneCount)
        }
    }

    override fun onResume() {
        super.onResume()
        loadDay()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(KEY_DONE_EXPANDED, doneExpanded)
    }

    /** Local midnight of today, built through the same helper the calendar
     *  tap handler uses so both agree on where a day starts. */
    private fun todayStartMs(): Long = CareQueue.dayStartMs(CareQueue.epochDayOf(System.currentTimeMillis()))

    private var lastDoneCount = 0

    private fun loadDay() {
        loadJob?.cancel()
        loadJob = lifecycleScope.launch {
            val nowMs = System.currentTimeMillis()
            val isToday = CareQueue.epochDayOf(dayStartMs) == CareQueue.epochDayOf(nowMs)
            val dateLabel = SimpleDateFormat("d MMMM", Locale.getDefault()).format(dayStartMs)
            binding.careDayTitle.text = if (isToday) {
                getString(R.string.calendar_today_prefix, dateLabel)
            } else {
                dateLabel.replaceFirstChar { it.uppercase() }
            }

            // Overdue-from-earlier-days: pending tasks whose due day is
            // strictly before today, surfaced here so they are never
            // silently invisible just because "today" moved past them.
            val overdue = if (isToday) {
                careRepository.today(nowMs).filter { it.task.dueAt < dayStartMs }
            } else {
                emptyList()
            }
            val todayTasks = careRepository.day(dayStartMs)
            val pending = todayTasks.filter { it.task.status == CareQueue.CareTask.Status.PENDING }
            val done = todayTasks.filter { it.task.status == CareQueue.CareTask.Status.DONE }
            lastDoneCount = done.size

            binding.careDaySubtitle.text =
                resources.getQuantityString(R.plurals.care_tasks_count, pending.size + overdue.size, pending.size + overdue.size)

            binding.careDayOverdueContainer.removeAllViews()
            binding.careDayOverdueContainer.visibility = if (overdue.isEmpty()) View.GONE else View.VISIBLE
            if (overdue.isNotEmpty()) {
                binding.careDayOverdueContainer.addView(sectionHeader(R.string.calendar_overdue_header))
                overdue.sortedBy { it.task.dueAt }.forEach { binding.careDayOverdueContainer.addView(buildRow(it)) }
            }

            binding.careDayPendingContainer.removeAllViews()
            pending.sortedWith(compareBy({ it.task.dueAt }, { it.plantName }))
                .forEach { binding.careDayPendingContainer.addView(buildRow(it)) }

            binding.careDayDoneContainer.removeAllViews()
            done.sortedBy { it.task.dueAt }.forEach { binding.careDayDoneContainer.addView(buildRow(it)) }
            updateToggleLabel(done.size)
            binding.careDayDoneContainer.visibility = if (doneExpanded) View.VISIBLE else View.GONE

            val nothingAtAll = overdue.isEmpty() && pending.isEmpty() && done.isEmpty()
            binding.careDayEmptyState.visibility = if (nothingAtAll) View.VISIBLE else View.GONE
        }
    }

    private fun updateToggleLabel(doneCount: Int) {
        binding.careDayToggleDone.visibility = if (doneCount == 0) View.GONE else View.VISIBLE
        binding.careDayToggleDone.text = getString(
            if (doneExpanded) R.string.calendar_hide_done else R.string.calendar_show_done,
            doneCount,
        )
    }

    private fun sectionHeader(@StringRes textRes: Int): View = TextView(this).apply {
        text = getString(textRes).uppercase()
        textSize = 11f
        letterSpacing = 0.06f
        setTextColor(getColor(R.color.disease_red))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = 8.dpPx() }
    }

    private fun buildRow(view: CareRepository.CareTaskView): View {
        val row = ItemCareTaskBinding.inflate(layoutInflater, binding.careDayPendingContainer, false)
        row.careTaskTime.text = DateFormat.getTimeFormat(this).format(Date(view.task.dueAt))
        row.careTaskIcon.setImageResource(iconFor(view.task.type))
        // CareRepository folds a missing custom reminder label into the raw
        // type key (title == type) before this screen ever sees it — resolve
        // that specific case to a localized preset label. A real custom
        // label or a treatment step's own text differs from the bare type
        // key, so those render as-is.
        row.careTaskTitle.text = when {
            view.task.type == "treatment" -> view.task.title
            view.task.title == view.task.type -> getString(ReminderPresets.fallbackLabelRes(view.task.type))
            else -> view.task.title
        }
        row.careTaskSubtitle.text = if (view.roomName != null) {
            "${view.plantName} · ${view.roomName}"
        } else {
            view.plantName
        }
        row.careTaskCheckbox.setOnCheckedChangeListener(null)
        row.careTaskCheckbox.isChecked = view.task.status == CareQueue.CareTask.Status.DONE
        row.careTaskCheckbox.isEnabled = view.task.status != CareQueue.CareTask.Status.DONE
        row.careTaskCheckbox.setOnClickListener {
            if (view.task.status == CareQueue.CareTask.Status.DONE) {
                row.careTaskCheckbox.isChecked = true
                return@setOnClickListener
            }
            // Prevent two completion coroutines (and two competing Undo
            // snackbars) when the checkbox is tapped rapidly.
            row.careTaskCheckbox.isEnabled = false
            Haptics.medium(this)
            completeTask(view)
        }
        row.root.setOnClickListener {
            Haptics.tick(this)
            openTaskDetail(view)
        }
        return row.root
    }

    private fun iconFor(type: String): Int = when (type) {
        "treatment" -> R.drawable.ic_treatment
        else -> ReminderPresets.iconRes(null, type)
    }

    private fun completeTask(view: CareRepository.CareTaskView) {
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            val record = withContext(NonCancellable) {
                val completed = careRepository.complete(view)
                if (completed != CareRepository.CompletionRecord.NoChange) {
                    rescheduleRecurring(view, afterCare = true)
                }
                completed
            }
            loadDay()
            if (record == CareRepository.CompletionRecord.NoChange) return@launch
            Snackbar.make(binding.careDayRoot, R.string.task_mark_done, Snackbar.LENGTH_LONG)
                .setAction(R.string.task_undo) {
                    lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
                        withContext(NonCancellable) {
                            careRepository.undo(record)
                            if (record != CareRepository.CompletionRecord.NoChange) {
                                rescheduleRecurring(view, afterCare = false)
                            }
                        }
                        loadDay()
                    }
                }
                .show()
        }
    }

    private suspend fun rescheduleRecurring(
        view: CareRepository.CareTaskView,
        afterCare: Boolean,
    ) {
        val id = view.task.id as? CareQueue.CareTask.TaskId.Recurring ?: return
        val reminder = careRepository.getReminder(id.reminderId) ?: return
        if (!reminder.enabled) return
        if (afterCare) {
            ReminderManager.rescheduleAfterCare(
                context = applicationContext,
                plantId = reminder.plantId,
                plantName = view.plantName,
                type = reminder.type,
                intervalDays = reminder.intervalDays,
                hour = reminder.hour,
                minute = reminder.minute,
                reminderId = reminder.id,
            )
        } else {
            ReminderManager.restoreAfterUndo(applicationContext, reminder, view.plantName)
        }
    }

    private fun openTaskDetail(view: CareRepository.CareTaskView) {
        val intent = Intent(this, CareTaskActivity::class.java)
        // Rows in the "done" section open the detail screen too — tell it so,
        // otherwise the recurring branch offers a second "Done" tap that
        // writes a duplicate journal entry and reminder event.
        intent.putExtra(
            CareTaskActivity.EXTRA_IS_DONE,
            view.task.status == CareQueue.CareTask.Status.DONE,
        )
        when (val id = view.task.id) {
            is CareQueue.CareTask.TaskId.Step -> {
                intent.putExtra(CareTaskActivity.EXTRA_TASK_KIND, "step")
                intent.putExtra(CareTaskActivity.EXTRA_STEP_ID, id.stepId)
            }
            is CareQueue.CareTask.TaskId.Recurring -> {
                intent.putExtra(CareTaskActivity.EXTRA_TASK_KIND, "recurring")
                intent.putExtra(CareTaskActivity.EXTRA_REMINDER_ID, id.reminderId)
                intent.putExtra(CareTaskActivity.EXTRA_DUE_AT, view.task.dueAt)
            }
        }
        startActivity(intent)
    }

    private fun Int.dpPx(): Int = (this * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_DAY_START_MS = "extra_day_start_ms"
        private const val KEY_DONE_EXPANDED = "care_day_done_expanded"
    }
}
