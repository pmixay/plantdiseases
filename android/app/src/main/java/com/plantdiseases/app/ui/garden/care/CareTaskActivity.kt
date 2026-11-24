package com.plantdiseases.app.ui.garden.care

import android.content.Context
import android.os.Bundle
import android.text.format.DateUtils
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DecodeFormat
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.databinding.ActivityCareTaskBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.util.ReminderPresets
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.Date

/**
 * Task detail — serves both [com.plantdiseases.app.util.CareQueue.CareTask]
 * kinds (recurring occurrence, treatment step). Sections render only when they have content: a plain watering
 * reminder has no products/recommendations/step-progress and must not
 * show empty headers for them.
 */
@AndroidEntryPoint
class CareTaskActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCareTaskBinding

    @javax.inject.Inject
    lateinit var careRepository: CareRepository

    @javax.inject.Inject
    lateinit var plantRepository: PlantRepository

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityCareTaskBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.careTaskScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.careTaskAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.careTaskScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.careTaskToolbar.setNavigationOnClickListener { finish() }
        binding.careTaskDoneButton.setOnClickListener { onDoneClicked() }
        binding.careTaskDoneButton.isEnabled = false

        loadTask()
    }

    private fun loadTask() {
        lifecycleScope.launch {
            when (intent.getStringExtra(EXTRA_TASK_KIND)) {
                "step" -> renderStep(intent.getLongExtra(EXTRA_STEP_ID, -1L))
                "recurring" -> renderRecurring(
                    intent.getLongExtra(EXTRA_REMINDER_ID, -1L),
                    intent.getLongExtra(EXTRA_DUE_AT, System.currentTimeMillis()),
                )
                else -> finish()
            }
        }
    }

    private suspend fun renderStep(stepId: Long) {
        val stepWithPlan = careRepository.getStep(stepId) ?: run {
            finish()
            return
        }
        val (step, plan) = stepWithPlan
        val plant = plantRepository.getPlant(plan.plantId) ?: run {
            finish()
            return
        }
        val isRu = LocaleHelper.isRussian(this)

        binding.careTaskTypeLabel.text = getString(R.string.task_type_treatment)
        binding.careTaskTimeLabel.text = android.text.format.DateFormat.getTimeFormat(this).format(Date(step.dueAt))
        bindPlantChip(plant.name, plant.coverPhotoUri)
        binding.careTaskPlantStep.visibility = View.VISIBLE
        binding.careTaskPlantStep.text = getString(
            R.string.treatment_step_of,
            step.stepIndex + 1,
            stepWithPlan.stepCount,
        ) + ", " + (if (isRu) plan.diseaseLabelRu else plan.diseaseLabelEn)

        bindTextSection(binding.careTaskDescriptionSection, binding.careTaskDescription, step.detail)
        bindBullets(step.bullets)
        bindConditions(plan.conditionsJson)
        bindProducts(step.productsJson)
        bindHistory(step)

        binding.careTaskDoneButton.isEnabled = step.status == "pending" && plan.status == "active"

        pendingComplete = {
            careRepository.complete(
                CareRepository.CareTaskView(
                    task = com.plantdiseases.app.util.CareQueue.CareTask(
                        id = com.plantdiseases.app.util.CareQueue.CareTask.TaskId.Step(step.id),
                        plantId = plant.id,
                        type = "treatment",
                        title = step.title,
                        dueAt = step.dueAt,
                        status = com.plantdiseases.app.util.CareQueue.CareTask.Status.PENDING,
                        stepIndex = step.stepIndex,
                    ),
                    plantName = plant.name,
                    roomName = null,
                    thumbnailSource = plant.coverPhotoUri,
                )
            )
        }
    }

    private suspend fun renderRecurring(reminderId: Long, dueAt: Long) {
        val reminder = careRepository.getReminder(reminderId) ?: run {
            finish()
            return
        }
        val plant = plantRepository.getPlant(reminder.plantId) ?: run {
            finish()
            return
        }

        binding.careTaskTypeLabel.text =
            ReminderPresets.displayLabel(reminder.label, reminder.type) { getString(it) }
        binding.careTaskTimeLabel.text = android.text.format.DateFormat.getTimeFormat(this).format(Date(dueAt))
        bindPlantChip(plant.name, plant.coverPhotoUri)
        binding.careTaskPlantStep.visibility = View.GONE

        bindTextSection(binding.careTaskDescriptionSection, binding.careTaskDescription, reminder.description)
        binding.careTaskBulletsSection.visibility = View.GONE
        binding.careTaskRecommendationsSection.visibility = View.GONE
        binding.careTaskProductsSection.visibility = View.GONE

        val lastDone = plantRepository.lastReminderDoneAt(reminder.plantId, reminder.type)
        if (lastDone != null) {
            binding.careTaskHistorySection.visibility = View.VISIBLE
            binding.careTaskLastDone.text = getString(R.string.task_last_done, relativeDate(lastDone))
        } else {
            binding.careTaskHistorySection.visibility = View.GONE
        }

        val epochDay = com.plantdiseases.app.util.CareQueue.epochDayOf(dueAt)
        pendingComplete = {
            val record = careRepository.complete(
                view = CareRepository.CareTaskView(
                    task = com.plantdiseases.app.util.CareQueue.CareTask(
                        id = com.plantdiseases.app.util.CareQueue.CareTask.TaskId.Recurring(reminderId, epochDay),
                        plantId = reminder.plantId,
                        type = reminder.type,
                        title = reminder.label ?: reminder.type,
                        dueAt = dueAt,
                        status = com.plantdiseases.app.util.CareQueue.CareTask.Status.PENDING,
                    ),
                    plantName = plant.name,
                    roomName = null,
                    thumbnailSource = plant.coverPhotoUri,
                ),
            )
            if (record != CareRepository.CompletionRecord.NoChange && reminder.enabled) {
                ReminderManager.rescheduleAfterCare(
                    context = applicationContext,
                    plantId = reminder.plantId,
                    plantName = plant.name,
                    type = reminder.type,
                    intervalDays = reminder.intervalDays,
                    hour = reminder.hour,
                    minute = reminder.minute,
                    reminderId = reminder.id,
                )
            }
            record
        }
        // Recurring occurrences have no row of their own, so there is no
        // status to read back here — the caller tells us whether this
        // occurrence is already done (the other two branches read step /
        // task status directly).
        binding.careTaskDoneButton.isEnabled = !intent.getBooleanExtra(EXTRA_IS_DONE, false)
    }

    private fun bindPlantChip(name: String, thumbnailSource: String?) {
        binding.careTaskPlantName.text = name
        binding.careTaskPlantThumb.clipToOutline = true
        if (thumbnailSource != null) {
            Glide.with(this)
                .load(thumbnailSource)
                .format(DecodeFormat.PREFER_RGB_565)
                .transform(CenterCrop(), RoundedCorners(resources.getDimensionPixelSize(R.dimen.radius_md)))
                .placeholder(R.drawable.ic_garden)
                .error(R.drawable.ic_garden)
                .into(binding.careTaskPlantThumb)
        } else {
            binding.careTaskPlantThumb.setImageResource(R.drawable.ic_garden)
        }
    }

    private fun bindTextSection(section: View, textView: TextView, text: String?) {
        if (text.isNullOrBlank()) {
            section.visibility = View.GONE
        } else {
            section.visibility = View.VISIBLE
            textView.text = text
        }
    }

    private fun bindBullets(bulletsJson: String?) {
        val bullets = parseJsonStringArray(bulletsJson)
        if (bullets.isEmpty()) {
            binding.careTaskBulletsSection.visibility = View.GONE
            return
        }
        binding.careTaskBulletsSection.visibility = View.VISIBLE
        binding.careTaskBulletsContainer.removeAllViews()
        bullets.forEach { bullet ->
            binding.careTaskBulletsContainer.addView(
                TextView(this).apply {
                    text = "•  $bullet"
                    textSize = 13f
                    setTextColor(getColor(R.color.on_surface))
                    setLineSpacing(3.dpPx().toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 4.dpPx() }
                }
            )
        }
    }

    private fun bindConditions(conditionsJson: String?) {
        if (conditionsJson.isNullOrBlank()) {
            binding.careTaskRecommendationsSection.visibility = View.GONE
            return
        }
        val obj = runCatching { JSONObject(conditionsJson) }.getOrNull()
        if (obj == null) {
            binding.careTaskRecommendationsSection.visibility = View.GONE
            return
        }
        binding.careTaskRecommendationsSection.visibility = View.VISIBLE
        binding.careTaskBestTime.text = obj.optString("best_time").takeIf { it.isNotBlank() }
        binding.careTaskBestTime.visibility = if (obj.has("best_time")) View.VISIBLE else View.GONE

        val tempArray = obj.optJSONArray("temp_c")
        if (tempArray != null && tempArray.length() == 2) {
            binding.careTaskTempRange.visibility = View.VISIBLE
            binding.careTaskTempRange.text = getString(
                R.string.task_temp_range,
                tempArray.getInt(0),
                tempArray.getInt(1),
            )
        } else {
            binding.careTaskTempRange.visibility = View.GONE
        }

        // 0 is a real value in the curated protocols (nutrient_deficiency) and
        // reads as a nonsensical "keep dry for 0 hours" — hide the row instead.
        val dryHours = obj.optInt("dry_hours", 0)
        if (dryHours > 0) {
            binding.careTaskDryHours.visibility = View.VISIBLE
            binding.careTaskDryHours.text =
                resources.getQuantityString(R.plurals.task_dry_hours, dryHours, dryHours)
        } else {
            binding.careTaskDryHours.visibility = View.GONE
        }
    }

    private fun bindProducts(productsJson: String?) {
        val array = runCatching { productsJson?.let { JSONArray(it) } }.getOrNull()
        if (array == null || array.length() == 0) {
            binding.careTaskProductsSection.visibility = View.GONE
            return
        }
        binding.careTaskProductsSection.visibility = View.VISIBLE
        binding.careTaskProductsContainer.removeAllViews()
        for (i in 0 until array.length()) {
            val product = array.optJSONObject(i) ?: continue
            binding.careTaskProductsContainer.addView(buildProductRow(product))
        }
    }

    private fun buildProductRow(product: JSONObject): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setBackgroundResource(R.drawable.bg_thumb_rounded)
        setPadding(12.dpPx(), 10.dpPx(), 12.dpPx(), 10.dpPx())
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 8.dpPx() }
        addView(
            TextView(this@CareTaskActivity).apply {
                text = product.optString("name")
                textSize = 14f
                setTextColor(getColor(R.color.on_surface))
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            }
        )
        addView(
            TextView(this@CareTaskActivity).apply {
                text = product.optString("active_ingredient")
                textSize = 12f
                setTextColor(getColor(R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 2.dpPx() }
            }
        )
        val note = product.optString("note")
        if (note.isNotBlank()) {
            addView(
                TextView(this@CareTaskActivity).apply {
                    text = note
                    textSize = 12f
                    setTextColor(getColor(R.color.on_surface))
                    setLineSpacing(2.dpPx().toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 4.dpPx() }
                }
            )
        }
    }

    private fun bindHistory(step: com.plantdiseases.app.data.local.TreatmentStepEntity) {
        if (step.status != "done" || step.doneAt == null) {
            binding.careTaskHistorySection.visibility = View.GONE
            return
        }
        binding.careTaskHistorySection.visibility = View.VISIBLE
        binding.careTaskLastDone.text = getString(R.string.task_last_done, relativeDate(step.doneAt))
    }

    private fun relativeDate(ms: Long): CharSequence =
        DateUtils.getRelativeTimeSpanString(ms, System.currentTimeMillis(), DateUtils.DAY_IN_MILLIS)

    private fun parseJsonStringArray(json: String?): List<String> {
        val array = runCatching { json?.let { JSONArray(it) } }.getOrNull() ?: return emptyList()
        return (0 until array.length()).mapNotNull { array.optString(it).takeIf { s -> s.isNotBlank() } }
    }

    private var pendingComplete: (suspend () -> CareRepository.CompletionRecord)? = null

    private fun onDoneClicked() {
        val action = pendingComplete ?: return
        if (!binding.careTaskDoneButton.isEnabled) return
        // Repository completion is idempotent, but two rapid taps used to
        // launch two coroutines and close the screen twice. Disable at the UI
        // boundary as soon as the accepted tap starts.
        binding.careTaskDoneButton.isEnabled = false
        Haptics.medium(this)
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            withContext(NonCancellable) { action() }
            finish()
        }
    }

    private fun Int.dpPx(): Int = (this * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_TASK_KIND = "extra_task_kind"
        const val EXTRA_STEP_ID = "extra_step_id"
        const val EXTRA_REMINDER_ID = "extra_reminder_id"
        const val EXTRA_DUE_AT = "extra_due_at"

        /** Recurring occurrences carry their done-state in the queue, not in a
         *  row this screen can re-read — the launcher passes it through. */
        const val EXTRA_IS_DONE = "extra_is_done"
    }
}
