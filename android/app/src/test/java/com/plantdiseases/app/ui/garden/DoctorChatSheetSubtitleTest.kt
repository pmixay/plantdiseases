package com.plantdiseases.app.ui.garden

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.util.LocaleHelper
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Covers [DoctorChatSheet.resolveSubtitle] — the label shown under the
 * "AI Plant Doctor" header. A scan session used to show PlantNet's (usually
 * English-only) common name unconditionally, leaking English text into the
 * Russian UI; this pins the locale-aware fallback (Cyrillic-checked common
 * name -> scientific name -> disease name) across both supported languages.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class DoctorChatSheetSubtitleTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private fun scan(
        speciesScientific: String? = null,
        speciesCommon: String? = null,
        diseaseName: String = "Leaf spot",
    ) = ScanEntity(
        imagePath = "/tmp/x.jpg",
        diseaseName = diseaseName,
        diseaseNameRu = "Пятнистость листьев",
        confidence = 0.9f,
        description = "d",
        descriptionRu = "д",
        treatment = "[]",
        treatmentRu = "[]",
        prevention = "[]",
        preventionRu = "[]",
        isHealthy = false,
        speciesScientific = speciesScientific,
        speciesCommon = speciesCommon,
    )

    @Test
    fun plantSession_alwaysShowsThePlantsOwnNameRegardlessOfLocale() {
        val plant = PlantEntity(name = "Fred", scientificName = "Ficus elastica", species = "Ficus")
        LocaleHelper.setLocale(context, "ru")

        assertThat(DoctorChatSheet.resolveSubtitle(plant, null, context)).isEqualTo("Fred")
    }

    @Test
    fun en_scanSession_prefersTheCommonName() {
        LocaleHelper.setLocale(context, "en")
        val s = scan(speciesScientific = "Sansevieria trifasciata", speciesCommon = "Snake plant")

        assertThat(DoctorChatSheet.resolveSubtitle(null, s, context)).isEqualTo("Snake plant")
    }

    @Test
    fun ru_scanSession_dropsAnEnglishOnlyCommonNameToTheScientificName() {
        LocaleHelper.setLocale(context, "ru")
        val s = scan(speciesScientific = "Sansevieria trifasciata", speciesCommon = "Snake plant")

        assertThat(DoctorChatSheet.resolveSubtitle(null, s, context)).isEqualTo("Sansevieria trifasciata")
    }

    @Test
    fun ru_scanSession_keepsAnAlreadyLocalisedCommonName() {
        LocaleHelper.setLocale(context, "ru")
        val s = scan(speciesScientific = "Sansevieria trifasciata", speciesCommon = "Сансевиерия")

        assertThat(DoctorChatSheet.resolveSubtitle(null, s, context)).isEqualTo("Сансевиерия")
    }

    @Test
    fun ru_scanSession_fallsBackToTheDiseaseNameWhenNoSpeciesWasIdentified() {
        LocaleHelper.setLocale(context, "ru")
        val s = scan(speciesScientific = null, speciesCommon = null, diseaseName = "Leaf spot")

        assertThat(DoctorChatSheet.resolveSubtitle(null, s, context)).isEqualTo("Leaf spot")
    }
}
