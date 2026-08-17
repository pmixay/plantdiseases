package com.plantdiseases.app.ui.guide

import android.text.Spannable
import android.text.Spanned
import android.text.style.LeadingMarginSpan
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Pins the hanging indent [GuideContentRenderer] applies to every bullet /
 * numbered line of a guide article — 542 such lines in GuideDataProvider, with no
 * coverage until now.
 *
 * The indent is written through a `tv.text as? Spannable` cast, so it depends on
 * `ContentLinks.apply` setting the text with `BufferType.SPANNABLE`; reverting that
 * to a plain `tv.text = out` stores an immutable SpannedString and every assertion
 * below drops to zero spans. Pinning that cross-package coupling is the point of
 * this file — its other half lives in ContentLinksTest.
 *
 * Two fixture mechanics that are easy to get wrong: the indent runs only inside a
 * section card, and `parseSections` opens a section only on a short all-caps line
 * ending in ':' — hence the "SYMPTOMS:" header rather than a bare bullet list. And
 * the body TextView is not a direct child of the container, so it is located by
 * content instead of by index, to avoid silently asserting against the span-free
 * header.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class GuideContentRendererTest {

    /** A header, two bullets, a blank separator, then a numbered line with no
     *  trailing newline — one paragraph per branch of the indent pass. */
    private val content = "SYMPTOMS:\n• a\n• b\n\n1. c"
    private val expectedBody = "• a\n• b\n\n1. c"

    private fun descendants(v: View): Sequence<View> = sequence {
        yield(v)
        if (v is ViewGroup) {
            for (i in 0 until v.childCount) yieldAll(descendants(v.getChildAt(i)))
        }
    }

    /** Renders [content] and returns the section card's body TextView. */
    private fun renderBody(): TextView {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        val container = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        GuideContentRenderer.render(container, content)

        val body = descendants(container)
            .filterIsInstance<TextView>()
            .firstOrNull { it.text.contains("•") }
        assertWithMessage("no body TextView rendered — does parseSections still open on 'SYMPTOMS:'?")
            .that(body).isNotNull()
        val tv = requireNotNull(body)
        assertThat(tv.text.toString()).isEqualTo(expectedBody)
        return tv
    }

    private fun marginSpans(tv: TextView): List<LeadingMarginSpan> {
        val text = tv.text as Spanned
        return text.getSpans(0, text.length, LeadingMarginSpan::class.java).toList()
    }

    @Test
    fun everyListLineGetsItsOwnLeadingMarginSpan() {
        // One span per paragraph, not one across the block: LeadingMarginSpan leaves
        // only its OWN first line unindented, so a single block-wide span would hang
        // just the first bullet and flatten the rest.
        assertThat(marginSpans(renderBody())).hasSize(3)
    }

    @Test
    fun theFinalLineIsIndented_andTheBlankLineIsNot() {
        val tv = renderBody()
        val text = tv.text as Spanned
        val ranges = marginSpans(tv)
            .map { text.getSpanStart(it) to text.getSpanEnd(it) }
            .sortedBy { it.first }

        // "• a" = [0,3), "• b" = [4,7), index 8 is the blank line and is skipped by
        // the `end > start` guard, "1. c" = [9,13) via the `indexOf('\n') == -1` tail.
        assertThat(ranges).containsExactly(0 to 3, 4 to 7, 9 to 13).inOrder()
    }

    @Test
    fun theBodyBufferStaysMutable_soTheIndentPassIsNotASilentNoOp() {
        // Guards the failure mode directly: on an immutable SpannedString the cast in
        // applyHangingIndent yields null, the indent vanishes, and CI stays green.
        assertWithMessage("ContentLinks.apply must leave a mutable buffer (BufferType.SPANNABLE)")
            .that(renderBody().text).isInstanceOf(Spannable::class.java)
    }
}
