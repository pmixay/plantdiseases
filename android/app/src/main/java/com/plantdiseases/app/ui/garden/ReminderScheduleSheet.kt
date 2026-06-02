package com.plantdiseases.app.ui.garden

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.TypedValue
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.databinding.ItemReminderRowBinding
import com.plantdiseases.app.databinding.SheetReminderScheduleBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.worker.PlantReminderWorker
import kotlinx.coroutines.launch

/**
 * Reminder schedule bottom sheet. The chrome (handle, header, three
 * reminder rows, save button) lives in ``sheet_reminder_schedule.xml``
 * with each row included from ``item_reminder_row.xml``. NumberPicker
 * ranges / formatters and all the persistence logic stay in code.
 */
class ReminderScheduleSheet : BottomSheetDialogFragment() {

    private var _binding: SheetReminderScheduleBinding? = null
    private val binding get() = _binding!!

    private lateinit var plant: PlantEntity
    private var pendingSaveAfterPermission = false

    /**
     * Optional care-plan defaults supplied by the caller. Applied once,
     * after Room reminders load, only for rows the user hasn't already
     * customised — manual edits always win over server suggestions.
     */
    private var carePlanDefaults: CarePlanResponse? = null

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!pendingSaveAfterPermission) return@registerForActivityResult
        pendingSaveAfterPermission = false
        if (granted) {
            persistReminders()
        } else {
            Toast.makeText(
                requireContext(),
                R.string.reminder_notifications_permission,
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    // Row wrappers — assigned in onViewCreated once the binding exists.
    private lateinit var waterRow: ReminderRow
    private lateinit var checkRow: ReminderRow
    private lateinit var fertilizeRow: ReminderRow

    /** Thin wrapper over an included row binding so the rest of the
     *  sheet keeps reading rows by name. */
    private inner class ReminderRow(private val b: ItemReminderRowBinding) {
        val switch get() = b.reminderSwitch
        val picker get() = b.reminderInterval
        val hourPicker get() = b.reminderHour
        val minutePicker get() = b.reminderMinute
        val intervalRow get() = b.reminderIntervalRow

        fun refreshLabels() {
            b.reminderIntervalLabel.text = getString(R.string.reminder_every_days, picker.value)
            b.reminderTimeLabel.text = getString(
                R.string.reminder_at_time,
                hourPicker.value,
                minutePicker.value * 15,
            )
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
        binding.reminderPlantName.text = plant.name

        waterRow = setupRow(binding.rowWater, R.drawable.ic_watering, R.string.reminder_watering)
        checkRow = setupRow(binding.rowCheck, R.drawable.ic_care, R.string.reminder_check)
        fertilizeRow = setupRow(binding.rowFertilize, R.drawable.ic_garden, R.string.reminder_fertilize)

        binding.reminderSaveBtn.setOnClickListener {
            Haptics.success(requireContext())
            saveReminders()
        }

        val behavior = BottomSheetBehavior.from(view.parent as View)
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
        behavior.peekHeight = 600.dp()
        loadReminders()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    /** Configure one included row: icon, label, picker ranges, formatters
     *  and the listeners that keep the labels + interval visibility live. */
    private fun setupRow(b: ItemReminderRowBinding, iconRes: Int, labelRes: Int): ReminderRow {
        b.reminderIcon.setImageResource(iconRes)
        b.reminderLabel.setText(labelRes)
        val row = ReminderRow(b)

        b.reminderInterval.apply { minValue = 1; maxValue = 30; value = 7 }
        b.reminderHour.apply {
            minValue = 0
            maxValue = 23
            value = 9
            setFormatter { v -> "%02d".format(v) }
        }
        b.reminderMinute.apply {
            minValue = 0
            maxValue = 3
            value = 0
            displayedValues = arrayOf("00", "15", "30", "45")
        }
        row.refreshLabels()

        val update = { row.refreshLabels() }
        b.reminderInterval.setOnValueChangedListener { _, _, _ -> update() }
        b.reminderHour.setOnValueChangedListener { _, _, _ -> update() }
        b.reminderMinute.setOnValueChangedListener { _, _, _ -> update() }
        b.reminderSwitch.setOnCheckedChangeListener { _, checked ->
            b.reminderIntervalRow.visibility = if (checked) View.VISIBLE else View.GONE
        }
        return row
    }

    private fun loadReminders() {
        val repo = (requireActivity().application as PlantDiseasesApp).plantRepository
        lifecycleScope.launch {
            val reminders = repo.getReminders(plant.id)
            val customisedTypes = mutableSetOf<String>()
            reminders.forEach { r ->
                val row = when (r.type) {
                    PlantReminderWorker.TYPE_WATERING -> waterRow
                    PlantReminderWorker.TYPE_CHECK -> checkRow
                    PlantReminderWorker.TYPE_FERTILIZE -> fertilizeRow
                    else -> return@forEach
                }
                row.switch.isChecked = r.enabled
                row.picker.value = r.intervalDays
                row.hourPicker.value = r.hour.coerceIn(0, 23)
                row.minutePicker.value = (r.minute / 15).coerceIn(0, 3)
                row.refreshLabels()
                row.intervalRow.visibility = if (r.enabled) View.VISIBLE else View.GONE
                customisedTypes += r.type
            }
            applyCarePlanDefaults(customisedTypes)
        }
    }

    /**
     * Care-plan-driven defaults. Only fills in rows the user hasn't already
     * saved a custom value for — saved reminders always take priority.
     *
     *  - watering:  `wateringDaysInt` if numeric, else leave the row alone
     *               (the server's `as_needed` cadence is intentionally not
     *               translated into a fixed reminder).
     *  - fertilise: `fertiliseWeeks * 7` days.
     *  - check:     half the watering cadence, capped to [3, 14] days, so
     *               diseased plants get a sane health-check rhythm out of
     *               the box.
     */
    private fun applyCarePlanDefaults(alreadyCustomised: Set<String>) {
        val plan = carePlanDefaults ?: return

        if (PlantReminderWorker.TYPE_WATERING !in alreadyCustomised) {
            plan.wateringDaysInt?.let { days ->
                waterRow.picker.value = days.coerceIn(waterRow.picker.minValue, waterRow.picker.maxValue)
                waterRow.switch.isChecked = true
                waterRow.intervalRow.visibility = View.VISIBLE
            }
        }
        if (PlantReminderWorker.TYPE_FERTILIZE !in alreadyCustomised && plan.fertiliseWeeks > 0) {
            val days = (plan.fertiliseWeeks * 7)
                .coerceIn(fertilizeRow.picker.minValue, fertilizeRow.picker.maxValue)
            fertilizeRow.picker.value = days
            fertilizeRow.switch.isChecked = true
            fertilizeRow.intervalRow.visibility = View.VISIBLE
        }
        if (PlantReminderWorker.TYPE_CHECK !in alreadyCustomised) {
            val baseDays = plan.wateringDaysInt ?: 7
            val checkDays = (baseDays / 2).coerceIn(3, 14)
            checkRow.picker.value = checkDays.coerceIn(
                checkRow.picker.minValue, checkRow.picker.maxValue
            )
            checkRow.switch.isChecked = true
            checkRow.intervalRow.visibility = View.VISIBLE
        }
        listOf(waterRow, checkRow, fertilizeRow).forEach { it.refreshLabels() }
    }

    private fun saveReminders() {
        if (needsNotificationPermission() && hasEnabledReminder()) {
            pendingSaveAfterPermission = true
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        persistReminders()
    }

    private fun persistReminders() {
        val repo = (requireActivity().application as PlantDiseasesApp).plantRepository
        val ctx = requireContext()

        lifecycleScope.launch {
            val entries = listOf(
                PlantReminderWorker.TYPE_WATERING to waterRow,
                PlantReminderWorker.TYPE_CHECK to checkRow,
                PlantReminderWorker.TYPE_FERTILIZE to fertilizeRow,
            )
            entries.forEach { (type, row) ->
                val enabled = row.switch.isChecked
                val days = row.picker.value
                val hour = row.hourPicker.value
                val minute = row.minutePicker.value * 15
                val savedId = repo.saveReminder(
                    PlantReminderEntity(
                        plantId = plant.id,
                        type = type,
                        intervalDays = days,
                        hour = hour,
                        minute = minute,
                        enabled = enabled,
                    )
                )
                if (enabled) {
                    ReminderManager.schedule(
                        ctx, plant.id, plant.name, type, days, hour, minute,
                        reminderId = savedId,
                    )
                } else {
                    ReminderManager.cancel(ctx, plant.id, type)
                }
            }
            Toast.makeText(ctx, getString(R.string.reminder_save), Toast.LENGTH_SHORT).show()
            dismiss()
        }
    }

    private fun hasEnabledReminder(): Boolean =
        waterRow.switch.isChecked || checkRow.switch.isChecked || fertilizeRow.switch.isChecked

    private fun needsNotificationPermission(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(
                requireContext(),
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics
    ).toInt()

    companion object {
        private const val TAG = "ReminderScheduleSheet"

        fun show(fm: FragmentManager, plant: PlantEntity) {
            val sheet = ReminderScheduleSheet().apply {
                this.plant = plant
            }
            sheet.show(fm, TAG)
        }

        /**
         * Open the sheet pre-populated with care-plan defaults (watering /
         * fertilising cadence, derived health-check interval). Existing saved
         * reminders for the plant still win — defaults are only applied to
         * the rows the user hasn't already customised.
         */
        fun showWithCarePlanDefaults(
            fm: FragmentManager,
            plant: PlantEntity,
            plan: CarePlanResponse,
        ) {
            val sheet = ReminderScheduleSheet().apply {
                this.plant = plant
                this.carePlanDefaults = plan
            }
            sheet.show(fm, TAG)
        }
    }
}
