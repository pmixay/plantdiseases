package com.plantdiseases.app.ui.species

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.appcompat.app.AppCompatActivity
import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.common.ExpandableSectionAnimator
import com.plantdiseases.app.ui.common.factRow
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Structural contract for the species screen's collapsible sections.
 *
 * SpeciesDetailActivity is `@AndroidEntryPoint`, and there is no Hilt test
 * runner in this module, so this inflates the layout directly instead of
 * launching the Activity. That still covers what hand-editing this 1000-line
 * layout actually breaks: a header or body landing outside its own card, a
 * duplicated id, or a body that does not start collapsed.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SpeciesSectionsLayoutTest {

    private data class SectionIds(
        val name: String,
        val card: Int,
        val header: Int,
        val chevron: Int,
        val body: Int,
    )

    private val sections = listOf(
        SectionIds("about", R.id.card_about, R.id.about_header, R.id.iv_about_chevron, R.id.about_body),
        SectionIds("care", R.id.card_care, R.id.care_header, R.id.iv_care_chevron, R.id.care_body),
        SectionIds(
            "fertiliser",
            R.id.card_fertiliser,
            R.id.fertiliser_header,
            R.id.iv_fertiliser_chevron,
            R.id.fertiliser_body,
        ),
        SectionIds(
            "diseases",
            R.id.card_diseases,
            R.id.diseases_header,
            R.id.iv_diseases_section_chevron,
            R.id.diseases_body,
        ),
        SectionIds(
            "flowering",
            R.id.card_flowering,
            R.id.flowering_header,
            R.id.iv_flowering_chevron,
            R.id.flowering_body,
        ),
        SectionIds(
            "nativeRange",
            R.id.card_native_range,
            R.id.native_range_header,
            R.id.iv_native_range_chevron,
            R.id.native_range_body,
        ),
        SectionIds("lunar", R.id.card_lunar, R.id.lunar_header, R.id.iv_lunar_chevron, R.id.lunar_body),
    )

    private fun inflate(): View {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        return LayoutInflater.from(activity).inflate(R.layout.activity_species_detail, null)
    }

    private fun View.isDescendantOf(ancestor: View): Boolean {
        var p = parent
        while (p is View) {
            if (p === ancestor) return true
            p = p.parent
        }
        return false
    }

    @Test
    fun everySectionHasAHeaderChevronAndBodyInsideItsOwnCard() {
        val root = inflate()
        for (s in sections) {
            val card = root.findViewById<View>(s.card)
            val header = root.findViewById<View>(s.header)
            val chevron = root.findViewById<View>(s.chevron)
            val body = root.findViewById<View>(s.body)
            assertWithMessage("${s.name}: card missing")
                .that(card).isNotNull()
            assertWithMessage("${s.name}: header missing")
                .that(header).isNotNull()
            assertWithMessage("${s.name}: chevron missing")
                .that(chevron).isNotNull()
            assertWithMessage("${s.name}: body missing")
                .that(body).isNotNull()
            // The animator walks up from the card, and toggles the body's
            // visibility — both only behave if they live inside that card.
            assertThat(header.isDescendantOf(card)).isTrue()
            assertThat(chevron.isDescendantOf(card)).isTrue()
            assertThat(body.isDescendantOf(card)).isTrue()
        }
    }

    @Test
    fun everySectionHeaderIsClickableAndMeetsTheTouchTarget() {
        val root = inflate()
        val minPx = root.resources.getDimensionPixelSize(R.dimen.species_info_touch_target)
        for (s in sections) {
            val header = root.findViewById<View>(s.header)
            assertWithMessage("${s.name}: header not clickable")
                .that(header.isClickable).isTrue()
            val declared = header.minimumHeight.coerceAtLeast(header.layoutParams?.height ?: 0)
            assertWithMessage("${s.name}: header below the touch target")
                .that(declared)
                .isAtLeast(minPx)
        }
    }

    @Test
    fun everySectionBodyStartsCollapsedInTheLayout() {
        val root = inflate()
        for (s in sections) {
            assertWithMessage("${s.name}: body should start GONE")
                .that(root.findViewById<View>(s.body).visibility).isEqualTo(View.GONE)
        }
    }

    @Test
    fun theCareCardStillOwnsTheRuntimeCarePlanSlot() {
        val root = inflate()
        val slot = root.findViewById<View>(R.id.care_slot)
        // loadCare() addViews the CarePlanCardView into care_slot; if the slot
        // ever escapes care_body, the card would show while collapsed.
        assertThat(slot).isNotNull()
        assertThat(slot.isDescendantOf(root.findViewById<View>(R.id.care_body))).isTrue()
    }

    @Test
    fun animatorTogglesBodyVisibilityAndChevronRotation() {
        val root = inflate()
        for (s in sections) {
            val card = root.findViewById<ViewGroup>(s.card)
            val chevron = root.findViewById<ImageView>(s.chevron)
            val body = root.findViewById<View>(s.body)

            ExpandableSectionAnimator.update(card, expanded = true, chevron = chevron, animate = false) {
                body.visibility = View.VISIBLE
            }
            assertWithMessage("${s.name}: not expanded")
                .that(body.visibility).isEqualTo(View.VISIBLE)
            assertThat(chevron.rotation).isEqualTo(180f)

            ExpandableSectionAnimator.update(card, expanded = false, chevron = chevron, animate = false) {
                body.visibility = View.GONE
            }
            assertWithMessage("${s.name}: not collapsed")
                .that(body.visibility).isEqualTo(View.GONE)
            assertThat(chevron.rotation).isEqualTo(0f)
        }
    }

    /** Regression: factRow's value column used to be WRAP_CONTENT with no
     *  weight, so in a horizontal LinearLayout it measured first against
     *  the FULL row width (the weighted label contributes nothing at that
     *  stage) — a long sentence value then claimed the whole row and the
     *  weight-1 label was left with little or nothing. Both columns are
     *  weighted now, so the label's share depends only on the row width,
     *  never on how long the value happens to be. */
    @Test
    fun factRowLabelKeepsItsShareAgainstALongWrappingValue() {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        val longValue = "2 части торфа или кокоса + 1 часть перлита + 1 часть коры для хорошего дренажа"
        val row = activity.factRow(R.drawable.ic_garden, "Концентрация", longValue) as ViewGroup

        val widthPx = (320 * activity.resources.displayMetrics.density).toInt()
        row.measure(
            View.MeasureSpec.makeMeasureSpec(widthPx, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        row.layout(0, 0, widthPx, row.measuredHeight)

        val label = row.getChildAt(1)
        assertWithMessage("label collapsed under a long value — factRow's weighting regressed")
            .that(label.measuredWidth).isGreaterThan(0)
    }
}
