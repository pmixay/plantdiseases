package com.plantdiseases.app.ui.garden.detail

import android.text.InputFilter
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.tabs.TabLayout
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.ui.garden.ReminderScheduleSheet
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.localizedSpeciesName
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Header / actions controller: the green hero panel, the four-tile action row
 * (+ "More" sheet), the tab strip, the care-logging dialogs, the
 * post-care reminder reschedule, and the delete-plant confirmation. Camera /
 * cover-pick stay in the Activity (lifecycle-bound launchers); this controller
 * triggers them via the [onRequestNewScan] callback.
 */
class DetailHeaderController(
    private val activity: AppCompatActivity,
    private val onReload: () -> Unit,
    private val onRequestNewScan: () -> Unit,
    private val onOpenHero: (PlantEntity, List<ScanEntity>) -> Unit,
) : KoinComponent {

    private val plantRepository: PlantRepository by inject()
    private var plantId: Long = -1L
    private var currentPlant: PlantEntity? = null

    fun bind(plantId: Long, currentPlant: PlantEntity?) {
        this.plantId = plantId
        this.currentPlant = currentPlant
    }

    fun buildHeader(
        plant: PlantEntity,
        scans: List<ScanEntity>,
        journal: List<PlantJournalEntity>,
    ): View {
        // Round only the BOTTOM corners. The hero stacks a cover photo on
        // top of the green info card; rounding the top corners cut awkward
        // arcs out of the real photo (it never sat flush with the card edge).
        // Full-bleed square top + rounded bottom reads as one clean hero
        // panel.
        val heroRadius = activity.resources.getDimension(R.dimen.radius_lg)
        val card = MaterialCardView(activity).apply {
            cardElevation = 0f
            shapeAppearanceModel = com.google.android.material.shape.ShapeAppearanceModel.builder()
                .setBottomLeftCorner(
                    com.google.android.material.shape.CornerFamily.ROUNDED,
                    heroRadius,
                )
                .setBottomRightCorner(
                    com.google.android.material.shape.CornerFamily.ROUNDED,
                    heroRadius,
                )
                .build()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        val outer = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
        }
        // Cover photo strip — user-picked first, latest-scan image
        // second, hide when neither exists so the text-only hero
        // still feels intentional rather than missing-image. The
        // gradient overlay at the bottom is rendered by the existing
        // bg_garden_hero drawable underneath, so the text remains
        // legible regardless of cover-photo brightness.
        val coverSrc: String? = plant.coverPhotoUri
            ?: scans.maxByOrNull { it.timestamp }?.imagePath
        if (coverSrc != null) {
            val cover = ImageView(activity).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    160.dp(),
                )
                scaleType = ImageView.ScaleType.CENTER_CROP
                clipToOutline = true
                isClickable = true
                setOnClickListener { onOpenHero(plant, scans) }
            }
            Glide.with(activity).load(coverSrc).into(cover)
            outer.addView(cover)
        }
        val body = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp(), 22.dp(), 20.dp(), 20.dp())
            setBackgroundResource(R.drawable.bg_garden_hero)
        }
        body.addView(
            TextView(activity).apply {
                text = plant.name
                textSize = 30f
                setTextColor(activity.getColor(R.color.hero_stat_text))
                // PictureThis-style bold hero on the vivid green gradient
                // — medium read as undersized once the header sat on a
                // saturated background.
                typeface = android.graphics.Typeface.create("sans-serif-black", android.graphics.Typeface.NORMAL)
                letterSpacing = -0.02f
                maxLines = 2
                ellipsize = android.text.TextUtils.TruncateAt.END
            },
        )
        body.addView(
            TextView(activity).apply {
                text = localizedSpeciesName(
                    plant.scientificName,
                    plant.commonName ?: plant.species,
                    LocaleHelper.isRussian(activity),
                ) ?: activity.getString(R.string.plant_species_unknown)
                textSize = 15f
                setTextColor(activity.getColor(R.color.hero_stat_text))
                alpha = 0.82f
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 4.dp() }
            },
        )
        val stats = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 18.dp() }
        }
        stats.addView(activity.statPill(activity.getString(R.string.scans), scans.size.toString()))
        stats.addView(activity.statPill(activity.getString(R.string.journal), journal.size.toString()))
        stats.addView(
            activity.statPill(
                activity.getString(R.string.waterings),
                journal.count { it.type == PlantRepository.JournalType.WATERING.value }.toString(),
                endMargin = false,
            ),
        )
        body.addView(stats)
        outer.addView(body)
        card.addView(outer)
        return card
    }

    /** Single-row action strip with the three most-used actions
     *  (Water, Note, Photo) plus a More tile that opens a sheet
     *  for the rarer logs (Fertilise, Prune, Repot, Mist) and
     *  Reminders. Eight chips on screen at once was overwhelming;
     *  this collapses to four equal tiles. */
    fun buildActionRow(): View = activity.actionRow(
        listOf(
            ActionSpec(R.string.log_watering, R.drawable.ic_watering) { showWateringDialog() },
            ActionSpec(R.string.add_note, R.drawable.ic_note_pencil) { showNoteDialog() },
            ActionSpec(R.string.new_scan, R.drawable.ic_scan) { onRequestNewScan() },
            ActionSpec(R.string.action_more, R.drawable.ic_clock) { showMoreActionsSheet() },
        ),
    ).apply {
        (layoutParams as LinearLayout.LayoutParams).topMargin = 12.dp()
    }

    /** Bottom sheet for the secondary care actions (and Reminders).
     *  Each row launches the same dialog the previous chip launched
     *  inline; this just defers the visual weight until the user
     *  needs it. */
    private fun showMoreActionsSheet() {
        val plant = currentPlant ?: return
        val items = listOf(
            ActionSpec(R.string.reminders, R.drawable.ic_clock) {
                ReminderScheduleSheet.show(activity.supportFragmentManager, plant)
            },
            ActionSpec(R.string.log_fertilising, R.drawable.ic_treatment) {
                showLogDialog(JournalAction.FERTILISE)
            },
            ActionSpec(R.string.log_pruning, R.drawable.ic_note_pencil) {
                showLogDialog(JournalAction.PRUNE)
            },
            ActionSpec(R.string.log_repotting, R.drawable.ic_garden) {
                showLogDialog(JournalAction.REPOT)
            },
            ActionSpec(R.string.log_misting, R.drawable.ic_warning) {
                showLogDialog(JournalAction.MIST)
            },
        )
        val sheet = com.google.android.material.bottomsheet.BottomSheetDialog(activity)
        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(8.dp(), 8.dp(), 8.dp(), 18.dp())
        }
        container.addView(
            TextView(activity).apply {
                text = activity.getString(R.string.action_more_title)
                textSize = 13f
                letterSpacing = 0.06f
                setTextColor(activity.getColor(R.color.on_surface_secondary))
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
                setPadding(16.dp(), 8.dp(), 16.dp(), 10.dp())
            },
        )
        items.forEach { spec ->
            container.addView(
                LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    isClickable = true
                    foreground = activity.selectable()
                    setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
                    addView(
                        ImageView(activity).apply {
                            setImageResource(spec.iconRes)
                            setColorFilter(activity.getColor(R.color.primary))
                            layoutParams = LinearLayout.LayoutParams(24.dp(), 24.dp())
                        },
                    )
                    addView(
                        TextView(activity).apply {
                            text = activity.getString(spec.labelRes)
                            textSize = 16f
                            setTextColor(activity.getColor(R.color.on_surface))
                            layoutParams = LinearLayout.LayoutParams(
                                LinearLayout.LayoutParams.MATCH_PARENT,
                                LinearLayout.LayoutParams.WRAP_CONTENT,
                            ).apply { marginStart = 16.dp() }
                        },
                    )
                    setOnClickListener {
                        Haptics.tick(activity)
                        sheet.dismiss()
                        spec.onClick()
                    }
                },
            )
        }
        sheet.setContentView(container)
        sheet.show()
    }

    /** Dialog kinds for the physiological-care journal actions. The
     *  existing watering / note / photo dialogs stay separate because
     *  each has its own bespoke flow (photo picker, etc.). */
    private enum class JournalAction(
        val titleRes: Int,
        val hintRes: Int,
        val iconRes: Int,
    ) {
        FERTILISE(R.string.log_fertilising, R.string.fertilising_hint, R.drawable.ic_garden),
        PRUNE(R.string.log_pruning, R.string.pruning_hint, R.drawable.ic_garden),
        REPOT(R.string.log_repotting, R.string.repotting_hint, R.drawable.ic_garden),
        MIST(R.string.log_misting, R.string.misting_hint, R.drawable.ic_watering),
    }

    private fun showLogDialog(kind: JournalAction) {
        val view = activity.layoutInflater.inflate(R.layout.dialog_gradient_input, null)
        view.findViewById<ImageView>(R.id.iv_dialog_icon).setImageResource(kind.iconRes)
        view.findViewById<TextView>(R.id.tv_dialog_title).setText(kind.titleRes)
        view.findViewById<TextInputLayout>(R.id.til_dialog_input).hint = activity.getString(kind.hintRes)
        val input = view.findViewById<TextInputEditText>(R.id.et_dialog_input).apply {
            filters = arrayOf(InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH))
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 2
            setSingleLine(false)
        }
        MaterialAlertDialogBuilder(activity)
            .setView(view)
            .setPositiveButton(R.string.reminder_save) { _, _ ->
                val raw = input.text?.toString()
                activity.lifecycleScope.launch {
                    when (kind) {
                        JournalAction.FERTILISE -> plantRepository.addFertilising(plantId, raw)
                        JournalAction.PRUNE -> plantRepository.addPruning(plantId, raw)
                        JournalAction.REPOT -> plantRepository.addRepotting(plantId, raw)
                        JournalAction.MIST -> plantRepository.addMisting(plantId, raw)
                    }
                    if (kind == JournalAction.FERTILISE) {
                        rescheduleReminderAfterCare(
                            com.plantdiseases.app.worker.PlantReminderWorker.TYPE_FERTILIZE,
                        )
                    }
                    onReload()
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    fun buildTabs(
        timelineContainer: () -> LinearLayout,
        photosContainer: () -> LinearLayout,
        journalContainer: () -> LinearLayout,
    ): TabLayout {
        val tabLayout = TabLayout(activity).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
            addTab(newTab().setText(R.string.timeline))
            addTab(newTab().setText(R.string.plant_photos))
            addTab(newTab().setText(R.string.plant_journal))
            addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
                override fun onTabSelected(tab: TabLayout.Tab) {
                    timelineContainer().visibility = if (tab.position == 0) View.VISIBLE else View.GONE
                    photosContainer().visibility = if (tab.position == 1) View.VISIBLE else View.GONE
                    journalContainer().visibility = if (tab.position == 2) View.VISIBLE else View.GONE
                }

                override fun onTabUnselected(tab: TabLayout.Tab) {}
                override fun onTabReselected(tab: TabLayout.Tab) {}
            })
        }
        return tabLayout
    }

    fun showNoteDialog() = showTextDialog(
        titleRes = R.string.add_note,
        hintRes = R.string.note_hint,
        iconRes = R.drawable.ic_note_pencil,
    ) { text ->
        activity.lifecycleScope.launch {
            plantRepository.addNote(plantId, text)
            onReload()
        }
    }

    fun showWateringDialog() = showTextDialog(
        titleRes = R.string.log_watering,
        hintRes = R.string.watering_hint,
        iconRes = R.drawable.ic_watering,
        allowEmpty = true,
    ) { text ->
        activity.lifecycleScope.launch {
            plantRepository.addWatering(plantId, text)
            rescheduleReminderAfterCare(
                com.plantdiseases.app.worker.PlantReminderWorker.TYPE_WATERING,
            )
            onReload()
        }
    }

    /**
     * "Mark watered/fertilised now" semantics: when the user logs the care
     * action by hand AND a matching reminder is enabled, push the periodic
     * schedule out so the next notification lands a full interval from now
     * — not on the stale cadence the morning after they already did the
     * work. No-op when the plant has no enabled reminder of this type.
     */
    private suspend fun rescheduleReminderAfterCare(type: String) {
        val plant = currentPlant ?: return
        val reminder = plantRepository.getReminders(plant.id)
            .firstOrNull { it.type == type && it.enabled } ?: return
        com.plantdiseases.app.util.ReminderManager.rescheduleAfterCare(
            context = activity,
            plantId = plant.id,
            plantName = plant.name,
            type = reminder.type,
            intervalDays = reminder.intervalDays,
            hour = reminder.hour,
            minute = reminder.minute,
            reminderId = reminder.id,
        )
        Toast.makeText(
            activity,
            activity.resources.getQuantityString(
                R.plurals.care_reminder_rescheduled,
                reminder.intervalDays,
                reminder.intervalDays,
            ),
            Toast.LENGTH_SHORT,
        ).show()
    }

    private fun showTextDialog(
        titleRes: Int,
        hintRes: Int,
        iconRes: Int = R.drawable.ic_garden,
        allowEmpty: Boolean = false,
        onSave: (String) -> Unit,
    ) {
        val view = activity.layoutInflater.inflate(R.layout.dialog_gradient_input, null)
        view.findViewById<ImageView>(R.id.iv_dialog_icon).setImageResource(iconRes)
        view.findViewById<TextView>(R.id.tv_dialog_title).setText(titleRes)
        val inputLayout = view.findViewById<TextInputLayout>(R.id.til_dialog_input).apply {
            hint = activity.getString(hintRes)
        }
        val input = view.findViewById<TextInputEditText>(R.id.et_dialog_input).apply {
            filters = arrayOf(InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            maxLines = 6
        }
        val dialog = MaterialAlertDialogBuilder(activity)
            .setView(view)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .show()
        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val text = input.text?.toString()?.trim().orEmpty()
            if (!allowEmpty && text.isEmpty()) {
                inputLayout.error = activity.getString(R.string.journal_text_required)
                return@setOnClickListener
            }
            onSave(text)
            dialog.dismiss()
        }
    }

    fun confirmDeletePlant() {
        val plant = currentPlant ?: return
        MaterialAlertDialogBuilder(activity)
            .setTitle(plant.name)
            .setMessage(R.string.delete_plant_confirm)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.delete) { _, _ ->
                activity.lifecycleScope.launch {
                    com.plantdiseases.app.util.ReminderManager.cancelAll(
                        activity,
                        plant.id,
                        // Generic reminders cancel by row id — fetch
                        // before the CASCADE delete wipes them.
                        plantRepository.getReminders(plant.id),
                    )
                    plantRepository.deletePlant(plant.id)
                    Toast.makeText(
                        activity,
                        R.string.plant_deleted,
                        Toast.LENGTH_SHORT,
                    ).show()
                    activity.finish()
                }
            }
            .show()
    }

    private fun Int.dp(): Int = dp(activity)

    private companion object {
        private const val TEXT_FIELD_MAX_LENGTH = 1024
    }
}
