package com.plantdiseases.app.ui.garden

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ImageView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.databinding.DialogReminderEditorBinding
import com.plantdiseases.app.databinding.ItemReminderRowBinding
import com.plantdiseases.app.databinding.SheetReminderScheduleBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.util.ReminderPresets
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import javax.inject.Inject

/**
 * Plant reminders bottom sheet — generic-reminder edition.
 *
 * Lists every reminder on the plant (icon + name + cadence + enable
 * switch); rows are user-composed via a small editor dialog (name,
 * optional description, preset icon, interval, time). The legacy
 * water / health-check / fertilise rows surface here as preset-backed
 * reminders: rows saved before schema v4 have NULL label/icon, and the
 * display falls back to their `type` via [ReminderPresets].
 *
 * The care-plan "apply to reminders" entry point still lands here via
 * [showWithCarePlanDefaults]; it creates/updates the watering +
 * fertilise + check preset reminders exactly like the old fixed rows,
 * with saved user customisations always winning over server defaults.
 */
@AndroidEntryPoint
class ReminderScheduleSheet : BottomSheetDialogFragment() {

    private var _binding: SheetReminderScheduleBinding? = null
    private val binding get() = _binding!!

    private lateinit var plant: PlantEntity

    /** Action deferred behind the POST_NOTIFICATIONS request. */
    private var pendingAfterPermission: (() -> Unit)? = null

    /**
     * Optional care-plan defaults supplied by the caller. Applied once,
     * after Room reminders load, only for preset types the user hasn't
     * already saved — manual edits always win over server suggestions.
     */
    private var carePlanDefaults: CarePlanResponse? = null

    @Inject
    lateinit var plantRepository: PlantRepository

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        val action = pendingAfterPermission ?: return@registerForActivityResult
        pendingAfterPermission = null
        if (granted) {
            action()
        } else {
            Toast.makeText(
                requireContext(),
                R.string.reminder_notifications_permission,
                Toast.LENGTH_SHORT,
            ).show()
            loadReminders() // reset any optimistic switch flips
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = SheetReminderScheduleBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        binding.reminderAddBtn.setOnClickListener {
            Haptics.medium(requireContext())
            showEditor(existing = null)
        }

        val behavior = BottomSheetBehavior.from(view.parent as View)
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
        behavior.peekHeight = 600.dp()

        // Resolve the plant. Normal path: show() set it directly. After a
        // config-change recreate (rotation / dark-mode / locale) or process
        // death the system rebuilds the fragment WITHOUT calling show(),
        // leaving `plant` uninitialised — fall back to the id stashed in
        // arguments and reload from Room instead of crashing on plant.name.
        // (carePlanDefaults are a transient convenience, intentionally not
        // restored; saved reminders still load.) Mirrors DoctorChatSheet.
        if (::plant.isInitialized) {
            bindPlant()
        } else {
            val savedId = arguments?.getLong(ARG_PLANT_ID, -1L) ?: -1L
            if (savedId <= 0L) {
                dismissAllowingStateLoss()
                return
            }
            lifecycleScope.launch {
                val restored = plantRepository.getPlant(savedId)
                if (restored == null || _binding == null) {
                    dismissAllowingStateLoss()
                    return@launch
                }
                plant = restored
                bindPlant()
            }
        }
    }

    private fun bindPlant() {
        binding.reminderPlantName.text = plant.name
        val defaults = carePlanDefaults
        if (defaults != null) {
            carePlanDefaults = null
            runWithNotificationPermission { applyCarePlanDefaults(defaults) }
        } else {
            loadReminders()
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    // ─── List ──────────────────────────────────────────────────────────

    private fun loadReminders() {
        if (!::plant.isInitialized) return
        lifecycleScope.launch {
            val reminders = plantRepository.getReminders(plant.id)
                .sortedWith(
                    compareByDescending<PlantReminderEntity> { it.enabled }
                        .thenBy { it.id }
                )
            val b = _binding ?: return@launch
            b.reminderList.removeAllViews()
            b.reminderEmpty.visibility =
                if (reminders.isEmpty()) View.VISIBLE else View.GONE
            reminders.forEach { reminder ->
                val row = ItemReminderRowBinding.inflate(
                    layoutInflater,
                    b.reminderList,
                    false,
                )
                bindRow(row, reminder)
                b.reminderList.addView(row.root)
            }
        }
    }

    private fun bindRow(row: ItemReminderRowBinding, reminder: PlantReminderEntity) {
        row.reminderIcon.setImageResource(
            ReminderPresets.iconRes(reminder.icon, reminder.type)
        )
        row.reminderLabel.text =
            ReminderPresets.displayLabel(reminder.label, reminder.type) { getString(it) }
        val meta = resources.getQuantityString(
            R.plurals.reminder_row_meta,
            reminder.intervalDays,
            reminder.intervalDays,
            reminder.hour,
            reminder.minute,
        )
        val description = reminder.description?.trim()?.takeIf { it.isNotEmpty() }
        row.reminderMeta.text =
            if (description != null) "$meta, $description" else meta

        row.reminderSwitch.setOnCheckedChangeListener(null)
        row.reminderSwitch.isChecked = reminder.enabled
        row.reminderSwitch.setOnCheckedChangeListener { _, checked ->
            Haptics.tick(requireContext())
            if (checked) {
                runWithNotificationPermission { setReminderEnabled(reminder, true) }
            } else {
                setReminderEnabled(reminder, false)
            }
        }
        row.reminderCard.setOnClickListener {
            Haptics.tick(requireContext())
            showEditor(reminder)
        }
    }

    private fun setReminderEnabled(reminder: PlantReminderEntity, enabled: Boolean) {
        val appContext = requireContext().applicationContext
        val plantName = plant.name
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            withContext(NonCancellable) {
                // Enabling creates a fresh WorkManager cadence, so persist the
                // same anchor the projected Do Today queue will read.
                val updated = reminder.copy(
                    enabled = enabled,
                    createdAt = if (enabled) System.currentTimeMillis() else reminder.createdAt,
                )
                plantRepository.saveReminderRow(updated)
                if (enabled) {
                    ReminderManager.schedule(appContext, updated, plantName)
                } else {
                    ReminderManager.cancelReminder(appContext, updated)
                }
            }
            if (isAdded) loadReminders()
        }
    }

    // ─── Editor ────────────────────────────────────────────────────────

    private fun showEditor(existing: PlantReminderEntity?) {
        if (!::plant.isInitialized) return
        val ctx = requireContext()
        val editor = DialogReminderEditorBinding.inflate(layoutInflater)

        editor.editorInterval.apply {
            minValue = 1
            maxValue = 30
            value = 7
        }
        editor.editorHour.apply {
            minValue = 0
            maxValue = 23
            value = 9
            setFormatter { v -> "%02d".format(v) }
        }
        editor.editorMinute.apply {
            minValue = 0
            maxValue = 3
            value = 0
            displayedValues = arrayOf("00", "15", "30", "45")
        }

        // Icon grid: single-select over the curated presets. For a NEW
        // reminder the selected preset also becomes the row's `type`
        // (back-compat semantics); editing never rewrites `type`.
        var selectedIconKey = existing?.icon
            ?: ReminderPresets.presetForType(existing?.type ?: ReminderPresets.TYPE_CUSTOM).key
        val cells = mutableMapOf<String, FrameLayout>()
        fun refreshSelection() {
            cells.forEach { (key, cell) ->
                cell.setBackgroundResource(
                    if (key == selectedIconKey) {
                        R.drawable.bg_icon_circle_selected
                    } else {
                        R.drawable.bg_icon_circle
                    }
                )
            }
        }
        // Track whether the name field still holds an untouched preset
        // label, so switching presets keeps prefilling it without ever
        // clobbering a user-typed name.
        var lastPresetLabel: String? = null
        ReminderPresets.presets.forEach { preset ->
            val cell = FrameLayout(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(44.dp(), 44.dp()).apply {
                    setMargins(4.dp(), 4.dp(), 4.dp(), 4.dp())
                }
                isClickable = true
                isFocusable = true
                contentDescription = getString(preset.labelRes)
                addView(
                    ImageView(ctx).apply {
                        setImageResource(preset.iconRes)
                        setColorFilter(ctx.getColor(R.color.primary))
                        layoutParams = FrameLayout.LayoutParams(22.dp(), 22.dp(), Gravity.CENTER)
                    }
                )
                setOnClickListener {
                    Haptics.tick(ctx)
                    selectedIconKey = preset.key
                    refreshSelection()
                    val current = editor.editorName.text?.toString()?.trim().orEmpty()
                    if (current.isEmpty() || current == lastPresetLabel) {
                        val label = getString(preset.labelRes)
                        editor.editorName.setText(label)
                        lastPresetLabel = label
                    }
                }
            }
            cells[preset.key] = cell
            editor.editorIconGrid.addView(cell)
        }

        // Prefill.
        if (existing != null) {
            editor.editorName.setText(
                ReminderPresets.displayLabel(existing.label, existing.type) { getString(it) }
            )
            editor.editorDescription.setText(existing.description.orEmpty())
            editor.editorInterval.value = existing.intervalDays.coerceIn(1, 30)
            editor.editorHour.value = existing.hour.coerceIn(0, 23)
            editor.editorMinute.value = (existing.minute / 15).coerceIn(0, 3)
        } else {
            val label = getString(ReminderPresets.presetForType(selectedIconKey).labelRes)
            editor.editorName.setText(label)
            lastPresetLabel = label
        }
        refreshSelection()

        fun refreshLabels() {
            editor.editorIntervalLabel.text =
                getString(R.string.reminder_every_days, editor.editorInterval.value)
            editor.editorTimeLabel.text = getString(
                R.string.reminder_at_time,
                editor.editorHour.value,
                editor.editorMinute.value * 15,
            )
        }
        refreshLabels()
        editor.editorInterval.setOnValueChangedListener { _, _, _ -> refreshLabels() }
        editor.editorHour.setOnValueChangedListener { _, _, _ -> refreshLabels() }
        editor.editorMinute.setOnValueChangedListener { _, _, _ -> refreshLabels() }

        val builder = MaterialAlertDialogBuilder(ctx)
            .setTitle(if (existing == null) R.string.reminder_new_title else R.string.reminder_edit_title)
            .setView(editor.root)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
        if (existing != null) {
            builder.setNeutralButton(R.string.reminder_delete) { _, _ ->
                deleteReminder(existing)
            }
        }
        val dialog = builder.show()

        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val name = editor.editorName.text?.toString()?.trim().orEmpty()
            if (name.isEmpty()) {
                editor.editorNameLayout.error = getString(R.string.reminder_name_required)
                return@setOnClickListener
            }
            val description = editor.editorDescription.text?.toString()?.trim()
                ?.takeIf { it.isNotEmpty() }
            val entity = (
                existing ?: PlantReminderEntity(
                    plantId = plant.id,
                    type = selectedIconKey, // preset key, or "custom" for the bell
                    enabled = true,
                )
                ).copy(
                label = name,
                description = description,
                icon = selectedIconKey,
                intervalDays = editor.editorInterval.value,
                hour = editor.editorHour.value,
                minute = editor.editorMinute.value * 15,
            )
            dialog.dismiss()
            if (entity.enabled) {
                runWithNotificationPermission { persistReminder(entity) }
            } else {
                persistReminder(entity)
            }
        }
    }

    private fun persistReminder(entity: PlantReminderEntity) {
        val appContext = requireContext().applicationContext
        val plantName = plant.name
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            withContext(NonCancellable) {
                // Saving an enabled reminder replaces its WorkManager schedule.
                // Re-anchor the database row at the same moment so the Garden
                // projection cannot show an occurrence earlier than the worker.
                val anchored = if (entity.enabled) {
                    entity.copy(createdAt = System.currentTimeMillis())
                } else {
                    entity
                }
                // New reminders for the legacy preset types merge into the
                // existing row of that type (one watering schedule per
                // plant — care-plan apply and quick-log depend on it).
                // Everything else is a plain row upsert by id.
                val savedId = if (anchored.id == 0L && ReminderPresets.isLegacyType(anchored.type)) {
                    plantRepository.saveReminder(anchored)
                } else {
                    plantRepository.saveReminderRow(anchored)
                }
                val saved = anchored.copy(id = savedId)
                if (saved.enabled) {
                    ReminderManager.schedule(appContext, saved, plantName)
                } else {
                    ReminderManager.cancelReminder(appContext, saved)
                }
            }
            context?.let { Toast.makeText(it, R.string.reminder_saved, Toast.LENGTH_SHORT).show() }
            if (isAdded) loadReminders()
        }
    }

    private fun deleteReminder(reminder: PlantReminderEntity) {
        val appContext = requireContext().applicationContext
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            withContext(NonCancellable) {
                ReminderManager.cancelReminder(appContext, reminder)
                plantRepository.deleteReminder(reminder.id)
            }
            context?.let { Toast.makeText(it, R.string.reminder_deleted, Toast.LENGTH_SHORT).show() }
            if (isAdded) loadReminders()
        }
    }

    // ─── Care-plan defaults ────────────────────────────────────────────

    /**
     * Care-plan-driven defaults. Creates/updates the three preset
     * reminders, but only for types the user hasn't already saved —
     * saved reminders always take priority.
     *
     *  - watering:  `wateringDaysInt` if numeric ("as_needed" cadence is
     *               intentionally not turned into a fixed reminder).
     *  - fertilise: `fertiliseWeeks * 7` days.
     *  - check:     half the watering cadence, capped to [3, 14] days.
     */
    private fun applyCarePlanDefaults(plan: CarePlanResponse) {
        val appContext = requireContext().applicationContext
        val plantId = plant.id
        val plantName = plant.name
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            val appliedCount = withContext(NonCancellable) {
                val existingTypes = plantRepository.getReminders(plantId).map { it.type }.toSet()
                val defaults = mutableListOf<PlantReminderEntity>()

                if (ReminderPresets.TYPE_WATERING !in existingTypes) {
                    plan.wateringDaysInt?.let { days ->
                        defaults += PlantReminderEntity(
                            plantId = plantId,
                            type = ReminderPresets.TYPE_WATERING,
                            intervalDays = days.coerceIn(1, 30),
                            enabled = true,
                        )
                    }
                }
                if (ReminderPresets.TYPE_FERTILIZE !in existingTypes && plan.fertiliseWeeks > 0) {
                    defaults += PlantReminderEntity(
                        plantId = plantId,
                        type = ReminderPresets.TYPE_FERTILIZE,
                        intervalDays = (plan.fertiliseWeeks * 7).coerceIn(1, 30),
                        enabled = true,
                    )
                }
                if (ReminderPresets.TYPE_CHECK !in existingTypes) {
                    val baseDays = plan.wateringDaysInt ?: 7
                    defaults += PlantReminderEntity(
                        plantId = plantId,
                        type = ReminderPresets.TYPE_CHECK,
                        intervalDays = (baseDays / 2).coerceIn(3, 14),
                        enabled = true,
                    )
                }

                defaults.forEach { entity ->
                    val savedId = plantRepository.saveReminder(entity)
                    ReminderManager.schedule(
                        appContext,
                        entity.copy(id = savedId),
                        plantName,
                    )
                }
                defaults.size
            }
            if (appliedCount > 0) {
                context?.let {
                    Toast.makeText(
                        it,
                        R.string.care_plan_reminders_applied,
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
            if (isAdded) loadReminders()
        }
    }

    // ─── Permission plumbing ───────────────────────────────────────────

    private fun runWithNotificationPermission(action: () -> Unit) {
        if (needsNotificationPermission()) {
            pendingAfterPermission = action
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            action()
        }
    }

    private fun needsNotificationPermission(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(
                requireContext(),
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics
    ).toInt()

    companion object {
        private const val TAG = "ReminderScheduleSheet"
        private const val ARG_PLANT_ID = "arg_plant_id"

        fun show(fm: FragmentManager, plant: PlantEntity) {
            val sheet = ReminderScheduleSheet().apply {
                this.plant = plant
                // Stash the id so the sheet self-heals after recreation.
                arguments = Bundle().apply { putLong(ARG_PLANT_ID, plant.id) }
            }
            sheet.show(fm, TAG)
        }

        /**
         * Open the sheet pre-populated with care-plan defaults (watering /
         * fertilising cadence, derived health-check interval). Existing saved
         * reminders for the plant still win — defaults are only applied to
         * preset types the user hasn't already customised.
         */
        fun showWithCarePlanDefaults(
            fm: FragmentManager,
            plant: PlantEntity,
            plan: CarePlanResponse,
        ) {
            val sheet = ReminderScheduleSheet().apply {
                this.plant = plant
                this.carePlanDefaults = plan
                arguments = Bundle().apply { putLong(ARG_PLANT_ID, plant.id) }
            }
            sheet.show(fm, TAG)
        }
    }
}
