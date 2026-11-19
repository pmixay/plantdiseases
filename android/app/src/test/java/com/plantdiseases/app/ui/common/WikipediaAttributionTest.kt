package com.plantdiseases.app.ui.common

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.SpeciesInfoResponse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** CC BY-SA requires the source and the licence next to reused Wikipedia text. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class WikipediaAttributionTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Test
    fun namesTheArticleAndTheLicence() {
        val text = WikipediaAttribution.message(context, "Monstera deliciosa", machineTranslated = false)
        assertThat(text).contains("Wikipedia")
        assertThat(text).contains("Monstera deliciosa")
        assertThat(text).contains("CC BY-SA 4.0")
        assertThat(text).contains("Wikimedia Commons")
        assertThat(text).doesNotContain("machine-translated")
    }

    @Test
    fun fallsBackWhenTheTitleIsMissing_andFlagsMachineTranslation() {
        val text = WikipediaAttribution.message(context, "  ", machineTranslated = true)
        assertThat(text).contains("CC BY-SA 4.0")
        assertThat(text).doesNotContain("“")
        assertThat(text).contains("machine-translated")
    }

    @Test
    @Config(qualifiers = "ru")
    fun russianCopy() {
        val text = WikipediaAttribution.message(context, "Монстера", machineTranslated = true)
        assertThat(text).contains("Википедии")
        assertThat(text).contains("«Монстера»")
        assertThat(text).contains("CC BY-SA 4.0")
        assertThat(text).contains("переведён автоматически")
    }

    @Test
    fun namesPhotoAuthorAndLicenceWhenTheServerSuppliesThem() {
        val text = WikipediaAttribution.message(
            context,
            "Rosa abietina",
            machineTranslated = false,
            photoCredit = "Jane Doe",
            photoLicense = "CC BY-SA 4.0",
        )
        assertThat(text).contains("Jane Doe")
        assertThat(text).contains("(CC BY-SA 4.0)")
    }

    @Test
    fun genusLevelExtractIsLabelled_speciesLevelIsNot() {
        val genus = SpeciesInfoResponse(
            available = true,
            title = "Rosa",
            extract = "Rosa is a genus.",
            level = "genus",
        )
        val species = genus.copy(level = "species")
        val labelled = WikipediaAttribution.displayExtract(context, genus)
        assertThat(labelled).contains("genus “Rosa”")
        assertThat(labelled).contains("Rosa is a genus.")
        assertThat(WikipediaAttribution.displayExtract(context, species))
            .isEqualTo("Rosa is a genus.")
        assertThat(WikipediaAttribution.displayExtract(context, genus.copy(extract = " "))).isNull()
    }

    @Test
    @Config(qualifiers = "ru")
    fun genusNoteIsRussianInRuLocale() {
        val genus = SpeciesInfoResponse(
            available = true,
            title = "Роза",
            extract = "Роза — род.",
            level = "genus",
        )
        assertThat(WikipediaAttribution.displayExtract(context, genus)).contains("«Роза»")
    }
}
