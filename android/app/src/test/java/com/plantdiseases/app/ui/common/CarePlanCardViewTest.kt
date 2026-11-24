package com.plantdiseases.app.ui.common

import android.app.Application
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanCheckpoint
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.HumidityBand
import com.plantdiseases.app.data.model.LuxBand
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The card's contract with its hosts: states, visibility rules and the
 * details panel the XML layouts (care_plan_*.xml) are bound into. The host is
 * a plain (non-Hilt) `AppCompatActivity`, since nothing here needs the graph.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class CarePlanCardViewTest {

    private fun card(): CarePlanCardView {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        val view = CarePlanCardView(activity)
        activity.setContentView(view)
        return view
    }

    private fun CarePlanCardView.view(id: Int): View = findViewById(id)

    private fun CarePlanCardView.text(id: Int): String = (view(id) as TextView).text.toString()

    private fun CarePlanCardView.visible(id: Int): Boolean = view(id).visibility == View.VISIBLE

    private fun CarePlanCardView.pillValue(pillId: Int): String =
        (view(pillId) as ViewGroup).findViewById<TextView>(R.id.care_plan_pill_value).text.toString()

    private fun CarePlanCardView.childCount(id: Int): Int = (view(id) as ViewGroup).childCount

    private val richPlan = CarePlanResponse(
        species = "Monstera",
        wateringDaysRaw = 7,
        fertiliseWeeks = 4,
        humidityTarget = "50-60 %",
        keyAction = "  Remove the affected leaves  ",
        followUpDays = 10,
        lightLuxTarget = LuxBand(min = 1000, max = 2500),
        placement = "Bright room",
        redFlags = listOf("Black spots", "   ", "Soft stem"),
        treatment = listOf("Spray weekly", "Isolate"),
        sanitation = listOf("Clean the pot"),
        isolation = true,
        checkpoints = listOf(CarePlanCheckpoint(day = 3, check = "Look for new spots"), CarePlanCheckpoint(0, " ")),
        seasonNote = "Water less in winter.",
    )

    @Test
    fun startsCollapsedWithNothingRevealed() {
        val view = card()

        assertThat(view.visible(R.id.care_plan_status)).isFalse()
        assertThat(view.visible(R.id.care_plan_details)).isFalse()
        assertThat(view.text(R.id.care_plan_title)).isEqualTo(view.context.getString(R.string.care_plan_title))
    }

    @Test
    fun curePlanRetitlesTheCard() {
        val view = card()

        view.isCurePlan = true

        assertThat(view.text(R.id.care_plan_title)).isEqualTo(view.context.getString(R.string.cure_plan_title))
        assertThat(view.view(R.id.care_plan_header).contentDescription.toString())
            .contains(view.context.getString(R.string.cure_plan_title))
    }

    @Test
    fun headerTapOpensAndClosesTheSummary() {
        val view = card()
        view.bind(richPlan, "Monstera", showApplyToReminders = false)

        view.view(R.id.care_plan_header).performClick()
        assertThat(view.visible(R.id.care_plan_status)).isTrue()

        view.view(R.id.care_plan_header).performClick()
        assertThat(view.visible(R.id.care_plan_status)).isFalse()
    }

    @Test
    fun bindFillsThePillsNoteAndFollowUp() {
        val view = card()

        view.bind(richPlan, "Monstera", showApplyToReminders = false)

        assertThat(view.visible(R.id.care_plan_pills)).isTrue()
        assertThat(view.visible(R.id.care_plan_loading)).isFalse()
        assertThat(view.visible(R.id.care_plan_error)).isFalse()
        val res = view.context.resources
        assertThat(view.pillValue(R.id.care_plan_pill_watering))
            .isEqualTo(res.getString(R.string.care_plan_watering_days, 7))
        assertThat(view.pillValue(R.id.care_plan_pill_fertilising))
            .isEqualTo(res.getString(R.string.care_plan_fertilising_weeks, 4))
        assertThat(view.pillValue(R.id.care_plan_pill_light))
            .isEqualTo(res.getString(R.string.care_plan_light_bright))
        assertThat(view.pillValue(R.id.care_plan_pill_humidity)).isEqualTo("50-60 %")
        assertThat(view.visible(R.id.care_plan_note)).isTrue()
        assertThat(view.text(R.id.care_plan_note_body)).isEqualTo("Remove the affected leaves")
        assertThat(view.text(R.id.care_plan_follow_up))
            .isEqualTo(res.getString(R.string.care_plan_follow_up, 10))
        assertThat(view.text(R.id.care_plan_subtitle))
            .isEqualTo(res.getString(R.string.care_plan_subtitle_species, "Monstera"))
    }

    @Test
    fun blankValuesAndKeyActionAreHandled() {
        val view = card()

        view.bind(CarePlanResponse(humidityTarget = ""), null, showApplyToReminders = false)

        assertThat(view.pillValue(R.id.care_plan_pill_humidity)).isEqualTo("-")
        assertThat(view.pillValue(R.id.care_plan_pill_watering)).isEqualTo("-")
        assertThat(view.visible(R.id.care_plan_note)).isFalse()
        assertThat(view.text(R.id.care_plan_subtitle))
            .isEqualTo(view.context.getString(R.string.care_plan_subtitle_unknown))
    }

    @Test
    fun offlineBasicPlanSaysSo() {
        val view = card()

        view.bind(richPlan, "Monstera", showApplyToReminders = false, offlineBasic = true)

        assertThat(view.text(R.id.care_plan_subtitle))
            .isEqualTo(view.context.getString(R.string.care_plan_subtitle_offline))
    }

    @Test
    fun applyButtonNeedsBothTheFlagAndACallback() {
        val view = card()
        var applied: CarePlanResponse? = null

        view.bind(richPlan, "Monstera", showApplyToReminders = true, onApplyToReminders = null)
        assertThat(view.visible(R.id.care_plan_apply)).isFalse()

        view.bind(richPlan, "Monstera", showApplyToReminders = false, onApplyToReminders = { applied = it })
        assertThat(view.visible(R.id.care_plan_apply)).isFalse()

        view.bind(richPlan, "Monstera", showApplyToReminders = true, onApplyToReminders = { applied = it })
        assertThat(view.visible(R.id.care_plan_apply)).isTrue()
        view.view(R.id.care_plan_apply).performClick()
        assertThat(applied).isSameInstanceAs(richPlan)
    }

    @Test
    fun loadingShowsOnlyTheSpinner() {
        val view = card()
        view.bind(richPlan, "Monstera", showApplyToReminders = true, onApplyToReminders = {})

        view.showLoading("Monstera")

        assertThat(view.visible(R.id.care_plan_loading)).isTrue()
        assertThat(view.visible(R.id.care_plan_error)).isFalse()
        assertThat(view.visible(R.id.care_plan_pills)).isFalse()
        assertThat(view.visible(R.id.care_plan_note)).isFalse()
        assertThat(view.visible(R.id.care_plan_follow_up)).isFalse()
        assertThat(view.visible(R.id.care_plan_apply)).isFalse()
        assertThat(view.visible(R.id.care_plan_details_toggle)).isFalse()
        assertThat(view.visible(R.id.care_plan_details)).isFalse()
    }

    @Test
    fun errorOffersRetryAndAMessage() {
        val view = card()
        var retries = 0

        view.showError(onRetry = { retries++ })
        assertThat(view.visible(R.id.care_plan_error)).isTrue()
        assertThat(view.text(R.id.care_plan_error_body))
            .isEqualTo(view.context.getString(R.string.care_plan_unavailable))
        assertThat(view.text(R.id.care_plan_retry)).isEqualTo(view.context.getString(R.string.care_plan_retry))
        view.view(R.id.care_plan_retry).performClick()
        assertThat(retries).isEqualTo(1)

        view.showError(onRetry = { retries++ }, message = "Offline")
        assertThat(view.text(R.id.care_plan_error_body)).isEqualTo("Offline")
    }

    @Test
    fun awaitingScanTurnsRetryIntoScanNow() {
        val view = card()
        var scans = 0
        view.showError(onRetry = {})

        view.showAwaitingScan("Monstera") { scans++ }

        assertThat(view.text(R.id.care_plan_error_body))
            .isEqualTo(view.context.getString(R.string.care_plan_awaiting_scan))
        assertThat(view.text(R.id.care_plan_retry)).isEqualTo(view.context.getString(R.string.care_plan_scan_now))
        view.view(R.id.care_plan_retry).performClick()
        assertThat(scans).isEqualTo(1)

        // A later error resets the awaiting-scan wording on the same instance.
        view.showError(onRetry = {})
        assertThat(view.text(R.id.care_plan_retry)).isEqualTo(view.context.getString(R.string.care_plan_retry))
    }

    @Test
    fun detailsToggleRevealsAndHidesThePanel() {
        val view = card()
        view.bind(richPlan, "Monstera", showApplyToReminders = false)
        view.view(R.id.care_plan_header).performClick()

        assertThat(view.visible(R.id.care_plan_details_toggle)).isTrue()
        assertThat(view.visible(R.id.care_plan_details)).isFalse()
        assertThat(view.text(R.id.care_plan_details_toggle))
            .isEqualTo(view.context.getString(R.string.care_plan_show_details))

        view.view(R.id.care_plan_details_toggle).performClick()
        assertThat(view.visible(R.id.care_plan_details)).isTrue()
        assertThat(view.text(R.id.care_plan_details_toggle))
            .isEqualTo(view.context.getString(R.string.care_plan_hide_details))

        view.view(R.id.care_plan_details_toggle).performClick()
        assertThat(view.visible(R.id.care_plan_details)).isFalse()
        assertThat((view.view(R.id.care_plan_details_toggle) as MaterialButton).text.toString())
            .isEqualTo(view.context.getString(R.string.care_plan_show_details))
    }

    @Test
    fun detailsPanelRendersEverySectionInOrder() {
        val view = card()

        view.bind(richPlan, "Monstera", showApplyToReminders = false)

        // Blank rows are dropped; bullets keep their prefix.
        assertThat(view.childCount(R.id.care_plan_red_flags_items)).isEqualTo(2)
        val firstFlag = (view.view(R.id.care_plan_red_flags_items) as ViewGroup).getChildAt(0) as TextView
        assertThat(firstFlag.text.toString()).endsWith("Black spots")
        assertThat(view.visible(R.id.care_plan_red_flags)).isTrue()
        assertThat(view.visible(R.id.care_plan_climate)).isTrue()
        assertThat(view.visible(R.id.care_plan_chip_light)).isTrue()
        assertThat(view.visible(R.id.care_plan_chip_humidity)).isFalse()
        assertThat(view.visible(R.id.care_plan_chip_temperature)).isFalse()
        assertThat(view.visible(R.id.care_plan_treatment)).isTrue()
        assertThat(view.visible(R.id.care_plan_sanitation)).isTrue()
        assertThat(view.visible(R.id.care_plan_isolation)).isTrue()
        assertThat(view.text(R.id.care_plan_isolation))
            .endsWith(view.context.getString(R.string.care_plan_isolation_required))
        assertThat(view.visible(R.id.care_plan_setup)).isTrue()
        // placement + the "how to feed" example (fertiliseWeeks > 0)
        assertThat(view.childCount(R.id.care_plan_setup_rows)).isEqualTo(2)
        assertThat(view.visible(R.id.care_plan_checkpoints)).isTrue()
        assertThat(view.childCount(R.id.care_plan_checkpoint_rows)).isEqualTo(1)
        assertThat(view.text(R.id.care_plan_season_note)).isEqualTo("Water less in winter.")

        // The order of the sections in the panel is the order the layout declares.
        val panel = view.view(R.id.care_plan_details) as ViewGroup
        fun indexOf(id: Int) = panel.indexOfChild(panel.findViewById(id))
        val order = listOf(
            R.id.care_plan_red_flags,
            R.id.care_plan_climate,
            R.id.care_plan_treatment,
            R.id.care_plan_sanitation,
            R.id.care_plan_isolation,
            R.id.care_plan_setup,
            R.id.care_plan_checkpoints,
            R.id.care_plan_season_note,
        ).map(::indexOf)
        assertThat(order).isInOrder()
        assertThat(order.first()).isAtLeast(0)
    }

    @Test
    fun rebindingWithASparserPlanHidesWhatIsGone() {
        val view = card()
        view.bind(richPlan, "Monstera", showApplyToReminders = false)

        view.bind(
            CarePlanResponse(humidityBand = HumidityBand(min = 40, max = 60), redFlags = listOf("Wilting")),
            "Monstera",
            showApplyToReminders = false,
        )

        assertThat(view.childCount(R.id.care_plan_red_flags_items)).isEqualTo(1)
        assertThat(view.visible(R.id.care_plan_chip_light)).isFalse()
        assertThat(view.visible(R.id.care_plan_chip_humidity)).isTrue()
        assertThat(view.visible(R.id.care_plan_treatment)).isFalse()
        assertThat(view.visible(R.id.care_plan_isolation)).isFalse()
        assertThat(view.visible(R.id.care_plan_setup)).isFalse()
        assertThat(view.visible(R.id.care_plan_checkpoints)).isFalse()
        assertThat(view.visible(R.id.care_plan_season_note)).isFalse()
    }

    @Test
    fun planWithoutDetailFieldsHasNoToggle() {
        val view = card()
        view.bind(CarePlanResponse(), "Monstera", showApplyToReminders = false)
        view.view(R.id.care_plan_header).performClick()

        assertThat(view.visible(R.id.care_plan_details_toggle)).isFalse()
    }

    @Test
    fun hostsCanTurnDetailsOff() {
        val view = card()
        view.detailsEnabled = false

        view.bind(richPlan, "Monstera", showApplyToReminders = false)
        view.view(R.id.care_plan_header).performClick()

        assertThat(view.visible(R.id.care_plan_details_toggle)).isFalse()
        assertThat(view.visible(R.id.care_plan_details)).isFalse()
        // Not built at all: the panel's containers stay empty.
        assertThat(view.childCount(R.id.care_plan_red_flags_items)).isEqualTo(0)
    }
}
