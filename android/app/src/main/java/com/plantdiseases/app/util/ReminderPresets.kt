package com.plantdiseases.app.util

import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import com.plantdiseases.app.R

/**
 * Curated catalogue of reminder presets for the generic-reminder
 * composer, plus the pure mapping logic that keeps pre-v4 rows
 * (label / icon == NULL) rendering correctly.
 *
 * BACK-COMPAT CONTRACT: the first three preset keys MUST stay equal to
 * [com.plantdiseases.app.worker.PlantReminderWorker.TYPE_WATERING] /
 * TYPE_CHECK / TYPE_FERTILIZE — care-plan "apply to reminders" and the
 * "mark watered/fertilised" quick-log re-anchor reminders by these
 * type strings.
 */
object ReminderPresets {

    const val TYPE_WATERING = "watering"
    const val TYPE_CHECK = "check"
    const val TYPE_FERTILIZE = "fertilize"
    const val TYPE_CUSTOM = "custom"

    /** The legacy fixed rows that predate generic reminders. They keep
     *  one-row-per-plant semantics and their original WorkManager
     *  unique-work names. */
    val LEGACY_TYPES = setOf(TYPE_WATERING, TYPE_CHECK, TYPE_FERTILIZE)

    data class Preset(
        /** Stable key, stored in both the `type` column (for reminders
         *  created from this preset) and the `icon` column. */
        val key: String,
        @field:DrawableRes val iconRes: Int,
        @field:StringRes val labelRes: Int,
    )

    /** Picker order: the three legacy presets first, generic bell last. */
    val presets: List<Preset> = listOf(
        Preset(TYPE_WATERING, R.drawable.ic_watering, R.string.reminder_watering),
        Preset(TYPE_FERTILIZE, R.drawable.ic_fertilize, R.string.reminder_fertilize),
        Preset(TYPE_CHECK, R.drawable.ic_care, R.string.reminder_check),
        Preset("mist", R.drawable.ic_mist, R.string.reminder_preset_mist),
        Preset("rotate", R.drawable.ic_rotate, R.string.reminder_preset_rotate),
        Preset("repot", R.drawable.ic_repot, R.string.reminder_preset_repot),
        Preset("prune", R.drawable.ic_prune, R.string.reminder_preset_prune),
        Preset("clean", R.drawable.ic_clean, R.string.reminder_preset_clean),
        Preset("light", R.drawable.ic_light, R.string.reminder_preset_light),
        Preset("humidity", R.drawable.ic_humidity, R.string.reminder_preset_humidity),
        Preset("temperature", R.drawable.ic_temperature, R.string.reminder_preset_temperature),
        Preset(TYPE_CUSTOM, R.drawable.ic_bell, R.string.reminder_preset_custom),
    )

    /** Exact preset for a key, or null when the key is unknown/null. */
    fun byKey(key: String?): Preset? = presets.firstOrNull { it.key == key }

    /** Legacy type → preset mapping. Unknown / free-form types fall back
     *  to the generic bell preset so old or hand-rolled rows always
     *  render something sensible. */
    fun presetForType(type: String?): Preset = byKey(type) ?: presets.last()

    /** Icon resolution: an explicit `icon` column wins, then the row's
     *  `type`, then the generic bell. */
    @DrawableRes
    fun iconRes(iconKey: String?, type: String?): Int =
        byKey(iconKey)?.iconRes ?: presetForType(type).iconRes

    /** String resource used when the row has no custom label (pre-v4
     *  rows and preset rows saved without renaming). */
    @StringRes
    fun fallbackLabelRes(type: String?): Int = presetForType(type).labelRes

    /**
     * Display-label derivation, kept resolver-based so it is unit
     * testable without an Android Context: a non-blank custom [label]
     * wins, otherwise the type-derived preset label is resolved.
     */
    fun displayLabel(label: String?, type: String?, resolve: (Int) -> String): String =
        label?.trim()?.takeIf { it.isNotEmpty() } ?: resolve(fallbackLabelRes(type))

    fun isLegacyType(type: String?): Boolean = type in LEGACY_TYPES
}
