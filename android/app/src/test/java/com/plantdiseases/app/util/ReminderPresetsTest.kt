package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.worker.PlantReminderWorker
import org.junit.Test

/**
 * Pure-logic tests for the generic-reminder preset catalogue:
 * legacy type → preset mapping and label/icon fallback derivation
 * for rows written before the v4 schema (label / icon == NULL).
 */
class ReminderPresetsTest {

    @Test
    fun `legacy preset keys match worker type constants`() {
        // Care-plan apply + rescheduleAfterCare key off these strings;
        // breaking them would orphan every pre-v4 reminder.
        assertThat(ReminderPresets.TYPE_WATERING).isEqualTo(PlantReminderWorker.TYPE_WATERING)
        assertThat(ReminderPresets.TYPE_CHECK).isEqualTo(PlantReminderWorker.TYPE_CHECK)
        assertThat(ReminderPresets.TYPE_FERTILIZE).isEqualTo(PlantReminderWorker.TYPE_FERTILIZE)
        assertThat(ReminderPresets.LEGACY_TYPES)
            .containsExactly("watering", "check", "fertilize")
    }

    @Test
    fun `legacy types map to their presets`() {
        assertThat(ReminderPresets.presetForType("watering").iconRes)
            .isEqualTo(R.drawable.ic_watering)
        assertThat(ReminderPresets.presetForType("watering").labelRes)
            .isEqualTo(R.string.reminder_watering)
        assertThat(ReminderPresets.presetForType("check").labelRes)
            .isEqualTo(R.string.reminder_check)
        assertThat(ReminderPresets.presetForType("fertilize").labelRes)
            .isEqualTo(R.string.reminder_fertilize)
    }

    @Test
    fun `unknown or null type falls back to the generic bell preset`() {
        listOf(null, "", "no_such_type", "custom").forEach { type ->
            val preset = ReminderPresets.presetForType(type)
            if (type == "custom") {
                assertThat(preset.key).isEqualTo(ReminderPresets.TYPE_CUSTOM)
            }
            assertThat(preset.iconRes).isEqualTo(R.drawable.ic_bell)
        }
    }

    @Test
    fun `explicit icon key wins over type derivation`() {
        assertThat(ReminderPresets.iconRes(iconKey = "mist", type = "watering"))
            .isEqualTo(R.drawable.ic_mist)
    }

    @Test
    fun `null icon key derives the icon from type`() {
        assertThat(ReminderPresets.iconRes(iconKey = null, type = "fertilize"))
            .isEqualTo(R.drawable.ic_fertilize)
        assertThat(ReminderPresets.iconRes(iconKey = null, type = "garbage"))
            .isEqualTo(R.drawable.ic_bell)
    }

    @Test
    fun `display label prefers a non-blank custom label`() {
        val resolved = ReminderPresets.displayLabel("  Misting orchids ", "watering") { "Watering" }
        assertThat(resolved).isEqualTo("Misting orchids")
    }

    @Test
    fun `display label falls back to the type-derived preset string`() {
        listOf(null, "", "   ").forEach { label ->
            val resolved = ReminderPresets.displayLabel(label, "watering") { res ->
                if (res == R.string.reminder_watering) "Watering" else "WRONG"
            }
            assertThat(resolved).isEqualTo("Watering")
        }
    }

    @Test
    fun `preset catalogue is unique and the legacy three come first`() {
        val keys = ReminderPresets.presets.map { it.key }
        assertThat(keys).containsNoDuplicates()
        assertThat(keys.take(3)).containsExactly("watering", "fertilize", "check")
        assertThat(keys.last()).isEqualTo(ReminderPresets.TYPE_CUSTOM)
        // The picker grid stays in the curated 10–14 range.
        assertThat(keys.size).isAtLeast(10)
        assertThat(keys.size).isAtMost(14)
    }
}
