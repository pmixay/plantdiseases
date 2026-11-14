package com.plantdiseases.app.ui.common

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
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
}
