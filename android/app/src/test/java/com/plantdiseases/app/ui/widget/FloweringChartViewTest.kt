package com.plantdiseases.app.ui.widget

import android.content.Context
import android.content.res.Configuration
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.Locale

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class FloweringChartViewTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Test
    fun currentMonthWithIntensity_bloomsNow() {
        val months = MutableList(12) { 0 }.apply { this[6] = 1 }

        assertThat(FloweringChartView.deriveBloomStatus(months, currentMonth = 6))
            .isEqualTo(FloweringChartView.BloomStatus.BloomsNow)
    }

    @Test
    fun nextBloom_usesNearestFutureMonth() {
        val months = MutableList(12) { 0 }.apply {
            this[4] = 2
            this[8] = 1
        }

        assertThat(FloweringChartView.deriveBloomStatus(months, currentMonth = 1))
            .isEqualTo(FloweringChartView.BloomStatus.NextBloom(monthIndex = 4))
    }

    @Test
    fun nextBloom_wrapsDecemberToJanuary() {
        val months = MutableList(12) { 0 }.apply { this[0] = 2 }
        val status = FloweringChartView.deriveBloomStatus(months, currentMonth = 11)

        assertThat(status)
            .isEqualTo(FloweringChartView.BloomStatus.NextBloom(monthIndex = 0))
        assertThat(
            FloweringChartView.statusText(
                localizedContext("en"),
                status!!,
            ),
        ).isEqualTo("Next bloom in January")
    }

    @Test
    fun allZeroSeries_isRareIndoorState() {
        assertThat(FloweringChartView.deriveBloomStatus(List(12) { 0 }, currentMonth = 3))
            .isEqualTo(FloweringChartView.BloomStatus.RarelyIndoors)
    }

    @Test
    fun malformedSeries_isRejectedRatherThanRenderedAsRare() {
        assertThat(FloweringChartView.deriveBloomStatus(List(11) { 0 }, currentMonth = 3))
            .isNull()
        assertThat(
            FloweringChartView.deriveBloomStatus(
                List(12) { if (it == 5) 3 else 0 },
                currentMonth = 3,
            ),
        ).isNull()

        val view = FloweringChartView(context)
        assertThat(view.setFlowering(List(11) { 0 }, monthIndex = 3)).isFalse()
        assertThat(view.statusText()).isNull()
    }

    @Test
    fun russianNextBloom_usesGrammaticalMonthName() {
        val text = FloweringChartView.statusText(
            localizedContext("ru"),
            FloweringChartView.BloomStatus.NextBloom(monthIndex = 2),
        )

        assertThat(text).isEqualTo("Следующее цветение — в марте")
    }

    @Test
    fun contentDescription_readsEveryMonthWithItsIntensity() {
        val months = MutableList(12) { 0 }.apply { this[4] = 2 }
        val view = FloweringChartView(localizedContext("en"))

        assertThat(view.setFlowering(months, monthIndex = 0)).isTrue()
        val description = view.contentDescription.toString()
        assertThat(description).contains("Next bloom in May")
        assertThat(description).contains("January: none")
        assertThat(description).contains("May: peak")
    }

    /** The status line needs "в марте", but the per-month list needs "март". */
    @Test
    fun russianContentDescription_namesMonthsInTheNominative() {
        val months = MutableList(12) { 0 }.apply {
            this[0] = 2
            this[1] = 1
        }
        val view = FloweringChartView(localizedContext("ru"))

        assertThat(view.setFlowering(months, monthIndex = 0)).isTrue()
        val description = view.contentDescription.toString()
        assertThat(description).contains("январь: пик")
        assertThat(description).contains("февраль: иногда")
        assertThat(description).contains("март: нет")
        assertThat(description).doesNotContain("январе")
    }

    private fun localizedContext(language: String): Context {
        val configuration = Configuration(context.resources.configuration)
        configuration.setLocale(Locale.forLanguageTag(language))
        return context.createConfigurationContext(configuration)
    }
}
