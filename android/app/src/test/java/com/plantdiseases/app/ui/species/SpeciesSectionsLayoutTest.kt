package com.plantdiseases.app.ui.species

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
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
        // Care is deliberately absent: it is a plain always-open card with no
        // chevron and no Section entry — see theCareCardIsNotCollapsible.
        SectionIds("about", R.id.card_about, R.id.about_header, R.id.iv_about_chevron, R.id.about_body),
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

    /**
     * The Care section is the one card on this screen that does NOT collapse:
     * folding the care plan away folds away the reason the screen exists. It
     * therefore has no chevron, an inert header, and a body that ships
     * visible — the exact inverse of every assertion above, so it needs its
     * own test rather than an exemption inside them.
     */
    @Test
    fun theCareCardIsNotCollapsible() {
        val root = inflate()
        val card = root.findViewById<View>(R.id.card_care)
        val header = root.findViewById<View>(R.id.care_header)
        val body = root.findViewById<View>(R.id.care_body)

        assertThat(card).isNotNull()
        assertThat(header).isNotNull()
        assertThat(body).isNotNull()
        assertThat(header.isDescendantOf(card)).isTrue()
        assertThat(body.isDescendantOf(card)).isTrue()

        assertWithMessage("care body must ship visible — nothing toggles it")
            .that(body.visibility).isEqualTo(View.VISIBLE)
        assertWithMessage("care header must not look tappable when it toggles nothing")
            .that(header.isClickable).isFalse()
    }

    @Test
    fun theCareCardStillOwnsTheRuntimeCarePlanSlot() {
        val root = inflate()
        val slot = root.findViewById<View>(R.id.care_slot)
        // loadCare() addViews the CarePlanCardView into care_slot; if the slot
        // ever escapes care_body it stops inheriting the card's padding and
        // the plan renders flush against the screen edge.
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

    /** Regression, twice over. The value column was once WRAP_CONTENT with no
     *  weight, so in a horizontal LinearLayout it measured first against the
     *  FULL row width (the weighted label contributes nothing at that stage)
     *  and a long sentence value starved the weight-1 label. The fix then
     *  over-corrected to 0dp on both children, which splits the row by weight
     *  alone regardless of content — a fixed, narrow label column that breaks
     *  long RU labels mid-word beside a short value in dead space.
     *
     *  The settled contract is WRAP_CONTENT + a weight on both: each column
     *  asks for what its content needs, and the weights only divide the
     *  surplus, so neither side is starved and neither is over-served.
     *
     *  This asserts the LayoutParams directly rather than a measured width:
     *  Robolectric's default (non-@GraphicsMode) text layout doesn't measure
     *  real glyph widths, so a measured-width assertion here would likely
     *  pass on the broken code too and prove nothing. */
    @Test
    fun factRowLabelAndValueAreBothWrapContentAndWeighted() {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        val row = activity.factRow(R.drawable.ic_garden, "Концентрация", "some value") as ViewGroup

        val label = row.getChildAt(1).layoutParams as LinearLayout.LayoutParams
        val value = row.getChildAt(2).layoutParams as LinearLayout.LayoutParams

        assertWithMessage("label column should be WRAP_CONTENT — a 0dp base ignores content width")
            .that(label.width).isEqualTo(ViewGroup.LayoutParams.WRAP_CONTENT)
        assertWithMessage("label column should carry a weight")
            .that(label.weight).isGreaterThan(0f)
        assertWithMessage("value column should be WRAP_CONTENT — a 0dp base ignores content width")
            .that(value.width).isEqualTo(ViewGroup.LayoutParams.WRAP_CONTENT)
        assertWithMessage("value column should carry a weight — unweighted, it starves the label")
            .that(value.weight).isGreaterThan(0f)
    }

    /** The other half of that contract. Equal weights only keep both columns
     *  alive while both fit on one line; a full sentence (the About card's
     *  native-range fact) still squeezed the label to zero width and left an
     *  unlabelled paragraph against the right edge. Long values now stack
     *  under the label instead, which is a different child structure: one
     *  column beside the icon rather than two siblings. */
    @Test
    fun factRowStacksALongValueUnderItsLabel() {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        val longValue = "Tomato originated in western South America and was domesticated in Mexico."
        val row = activity.factRow(R.drawable.ic_moon, "Native range", longValue) as ViewGroup

        assertWithMessage("a stacked row is icon + one column, not icon + label + value")
            .that(row.childCount).isEqualTo(2)
        val column = row.getChildAt(1) as ViewGroup
        assertWithMessage("the column takes the width the icon leaves, so the value can wrap")
            .that((column.layoutParams as LinearLayout.LayoutParams).width).isEqualTo(0)
        assertWithMessage("label then value, both full width of the column")
            .that(column.childCount).isEqualTo(2)
        assertThat((column.getChildAt(0) as android.widget.TextView).text.toString())
            .isEqualTo("Native range")
        assertThat((column.getChildAt(1) as android.widget.TextView).text.toString())
            .isEqualTo(longValue)
    }
}
