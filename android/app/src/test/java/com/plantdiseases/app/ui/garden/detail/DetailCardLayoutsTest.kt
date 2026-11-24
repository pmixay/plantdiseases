package com.plantdiseases.app.ui.garden.detail

import android.app.Application
import android.view.View
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.DetailAdaptiveWateringCardBinding
import com.plantdiseases.app.databinding.DetailEntryCardBinding
import com.plantdiseases.app.databinding.DetailHealthCardBinding
import com.plantdiseases.app.databinding.DetailLastWateredStripBinding
import com.plantdiseases.app.databinding.DetailProgressionCardBinding
import com.plantdiseases.app.databinding.DetailTreatmentPlanCardBinding
import com.plantdiseases.app.databinding.DetailTreatmentStepRowBinding
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Structural contract for the plant-detail extra cards that used to be built in
 * code. Each layout is inflated against a vertical LinearLayout parent exactly
 * as [DetailExtraCardsController] does, so the card margins survive and the
 * state a controller toggles starts in its expected default.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class DetailCardLayoutsTest {

    private val activity: AppCompatActivity =
        Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()

    private val parent = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }

    private fun px(dimen: Int) = activity.resources.getDimensionPixelSize(dimen)

    private fun topMargin(view: View) = (view.layoutParams as LinearLayout.LayoutParams).topMargin

    @Test
    fun entryCard_keepsParentLayoutParamsAndIsClickable() {
        val binding = DetailEntryCardBinding.inflate(activity.layoutInflater, parent, false)

        assertThat(topMargin(binding.root)).isEqualTo(px(R.dimen.detail_card_gap))
        assertThat(binding.root.isClickable).isTrue()
        assertThat(binding.detailEntrySubtitle.maxLines).isEqualTo(2)
    }

    @Test
    fun adaptiveWateringCard_hasBothActions() {
        val binding = DetailAdaptiveWateringCardBinding.inflate(activity.layoutInflater, parent, false)

        assertThat(topMargin(binding.root)).isEqualTo(px(R.dimen.detail_card_gap))
        assertThat(binding.detailAdaptiveDismiss.text.toString())
            .isEqualTo(activity.getString(R.string.adaptive_watering_dismiss))
        assertThat(binding.detailAdaptiveApply.isClickable).isTrue()
    }

    @Test
    fun lastWateredStrip_isAPlainTextRowWithTopGap() {
        val strip = DetailLastWateredStripBinding.inflate(activity.layoutInflater, parent, false).root

        assertThat(topMargin(strip)).isEqualTo(px(R.dimen.detail_strip_gap))
    }

    @Test
    fun progressionCard_startsInItsLoadingState() {
        val binding = DetailProgressionCardBinding.inflate(activity.layoutInflater, parent, false)

        assertThat(topMargin(binding.root)).isEqualTo(px(R.dimen.detail_card_gap_lg))
        assertThat(binding.detailProgressionTrend.text.toString())
            .isEqualTo(activity.getString(R.string.progression_loading))
        assertThat(binding.detailProgressionMeta.text.toString()).isEmpty()
    }

    @Test
    fun healthCard_hidesScoreStateUntilBound() {
        val binding = DetailHealthCardBinding.inflate(activity.layoutInflater, parent, false)

        assertThat(binding.detailHealthScore.visibility).isEqualTo(View.GONE)
        assertThat(binding.detailHealthEmpty.visibility).isEqualTo(View.GONE)
        assertThat(binding.detailHealthDelta.visibility).isEqualTo(View.GONE)
        assertThat(binding.detailHealthSparkline.visibility).isEqualTo(View.GONE)
    }

    @Test
    fun treatmentPlanCard_startsActiveWithAnEmptyStepList() {
        val binding = DetailTreatmentPlanCardBinding.inflate(activity.layoutInflater, parent, false)

        assertThat(binding.detailTreatmentFinished.visibility).isEqualTo(View.GONE)
        assertThat(binding.detailTreatmentActive.visibility).isEqualTo(View.VISIBLE)
        assertThat(binding.detailTreatmentSteps.childCount).isEqualTo(0)
    }

    @Test
    fun treatmentStepRow_isClickableWithTopGap() {
        val binding = DetailTreatmentStepRowBinding.inflate(activity.layoutInflater, parent, false)

        assertThat(binding.root.isClickable).isTrue()
        assertThat(topMargin(binding.root)).isEqualTo(px(R.dimen.detail_treatment_step_gap))
    }
}
