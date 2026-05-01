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
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.NumberPicker
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.worker.PlantReminderWorker
import kotlinx.coroutines.launch

class ReminderScheduleSheet : BottomSheetDialogFragment() {

    private lateinit var plant: PlantEntity
    private var pendingSaveAfterPermission = false

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

    // Row wrappers
    private val waterRow = ReminderRow()
    private val checkRow = ReminderRow()
    private val fertilizeRow = ReminderRow()

    /** Holds the mutable state for one reminder row. */
    private inner class ReminderRow {
        lateinit var switch: Switch
        lateinit var picker: NumberPicker
        lateinit var hourPicker: NumberPicker
        lateinit var minutePicker: NumberPicker
        lateinit var intervalLabel: TextView
        lateinit var timeLabel: TextView
        lateinit var intervalRow: LinearLayout

        fun refreshLabels() {
            intervalLabel.text = getString(R.string.reminder_every_days, picker.value)
            timeLabel.text = getString(
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
    ): View = buildUI()

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val behavior = BottomSheetBehavior.from(view.parent as View)
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
        behavior.peekHeight = 600.dp()
        loadReminders()
    }

    private fun buildUI(): View {
        val root = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(requireContext().getColor(R.color.background))
        }

        // Drag handle
        root.addView(View(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_handle)
            layoutParams = LinearLayout.LayoutParams(40.dp(), 4.dp()).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = 8.dp()
                bottomMargin = 4.dp()
            }
        })

        // Header (green gradient)
        root.addView(buildHeader())

        // Subtitle
        root.addView(TextView(requireContext()).apply {
            text = getString(R.string.reminder_subtitle)
            textSize = 13f
            setTextColor(requireContext().getColor(R.color.on_surface_secondary))
            setPadding(20.dp(), 14.dp(), 20.dp(), 2.dp())
        })

        // Three reminder rows
        val rowsContainer = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 8.dp(), 16.dp(), 0)
        }
        rowsContainer.addView(buildReminderRowView(R.drawable.ic_watering, R.string.reminder_watering, waterRow))
        rowsContainer.addView(buildReminderRowView(R.drawable.ic_care, R.string.reminder_check, checkRow))
        rowsContainer.addView(buildReminderRowView(R.drawable.ic_garden, R.string.reminder_fertilize, fertilizeRow))
        root.addView(rowsContainer)

        // Save button
        root.addView(MaterialButton(requireContext()).apply {
            text = getString(R.string.reminder_save)
            cornerRadius = 20.dp()
            setBackgroundColor(requireContext().getColor(R.color.primary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                52.dp(),
            ).apply { setMargins(16.dp(), 20.dp(), 16.dp(), 24.dp()) }
            setOnClickListener {
                Haptics.success(requireContext())
                saveReminders()
            }
        })

        return root
    }

    private fun buildHeader(): View {
        val header = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(20.dp(), 20.dp(), 20.dp(), 20.dp())
            setBackgroundResource(R.drawable.bg_garden_hero)
        }
        header.addView(FrameLayout(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            layoutParams = LinearLayout.LayoutParams(48.dp(), 48.dp())
            addView(ImageView(requireContext()).apply {
                setImageResource(R.drawable.ic_care)
                setColorFilter(requireContext().getColor(R.color.primary))
                layoutParams = FrameLayout.LayoutParams(26.dp(), 26.dp(), Gravity.CENTER)
            })
        })
        val titles = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 14.dp() }
        }
        titles.addView(TextView(requireContext()).apply {
            text = getString(R.string.reminders)
            textSize = 20f
            setTextColor(requireContext().getColor(R.color.on_primary))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        })
        titles.addView(TextView(requireContext()).apply {
            text = plant.nickname?.takeIf { it.isNotBlank() } ?: plant.name
            textSize = 14f
            setTextColor(requireContext().getColor(android.R.color.white))
            alpha = 0.8f
        })
        header.addView(titles)
        return header
    }

    private fun buildReminderRowView(
        iconRes: Int,
        labelRes: Int,
        row: ReminderRow,
    ): View {
        val card = MaterialCardView(requireContext()).apply {
            radius = 16.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = requireContext().getColor(R.color.card_stroke)
            setCardBackgroundColor(requireContext().getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 10.dp() }
        }
        val col = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
        }

        // Icon + label + switch row
        val topRow = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        topRow.addView(FrameLayout(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            layoutParams = LinearLayout.LayoutParams(40.dp(), 40.dp())
            addView(ImageView(requireContext()).apply {
                setImageResource(iconRes)
                setColorFilter(requireContext().getColor(R.color.primary))
                layoutParams = FrameLayout.LayoutParams(22.dp(), 22.dp(), Gravity.CENTER)
            })
        })
        topRow.addView(TextView(requireContext()).apply {
            text = getString(labelRes)
            textSize = 16f
            setTextColor(requireContext().getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 12.dp() }
        })

        row.switch = Switch(requireContext()).apply {
            thumbTintList = android.content.res.ColorStateList.valueOf(
                requireContext().getColor(R.color.primary)
            )
        }
        topRow.addView(row.switch)
        col.addView(topRow)

        row.intervalRow = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            visibility = if (row.switch.isChecked) View.VISIBLE else View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
        row.intervalLabel = TextView(requireContext()).apply {
            textSize = 13f
            setTextColor(requireContext().getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        row.picker = NumberPicker(requireContext()).apply {
            minValue = 1; maxValue = 30; value = 7
            layoutParams = LinearLayout.LayoutParams(80.dp(), LinearLayout.LayoutParams.WRAP_CONTENT)
        }
        row.timeLabel = TextView(requireContext()).apply {
            textSize = 13f
            setTextColor(requireContext().getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        row.hourPicker = NumberPicker(requireContext()).apply {
            minValue = 0
            maxValue = 23
            value = 9
            setFormatter { value -> "%02d".format(value) }
            layoutParams = LinearLayout.LayoutParams(70.dp(), LinearLayout.LayoutParams.WRAP_CONTENT)
        }
        row.minutePicker = NumberPicker(requireContext()).apply {
            minValue = 0
            maxValue = 3
            value = 0
            displayedValues = arrayOf("00", "15", "30", "45")
            layoutParams = LinearLayout.LayoutParams(70.dp(), LinearLayout.LayoutParams.WRAP_CONTENT)
        }
        val updateLabel = { row.refreshLabels() }
        updateLabel()
        row.picker.setOnValueChangedListener { _, _, _ -> updateLabel() }
        row.hourPicker.setOnValueChangedListener { _, _, _ -> updateLabel() }
        row.minutePicker.setOnValueChangedListener { _, _, _ -> updateLabel() }

        row.intervalRow.addView(LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(row.intervalLabel)
            addView(row.picker)
        })
        row.intervalRow.addView(LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 6.dp() }
            addView(row.timeLabel)
            addView(row.hourPicker)
            addView(TextView(requireContext()).apply {
                text = ":"
                textSize = 18f
                setTextColor(requireContext().getColor(R.color.on_surface_secondary))
            })
            addView(row.minutePicker)
        })
        col.addView(row.intervalRow)

        row.switch.setOnCheckedChangeListener { _, checked ->
            row.intervalRow.visibility = if (checked) View.VISIBLE else View.GONE
        }

        card.addView(col)
        return card
    }

    private fun loadReminders() {
        val repo = (requireActivity().application as PlantDiseasesApp).plantRepository
        lifecycleScope.launch {
            val reminders = repo.getReminders(plant.id)
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
            }
        }
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
                repo.saveReminder(
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
                    ReminderManager.schedule(ctx, plant.id, plant.name, type, days, hour, minute)
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
    }
}
