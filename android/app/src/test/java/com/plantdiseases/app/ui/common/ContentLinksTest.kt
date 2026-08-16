package com.plantdiseases.app.ui.common

import android.text.Spannable
import android.text.Spanned
import android.text.style.ClickableSpan
import android.widget.TextView
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** Verifies the `[label](token)` cross-link parser ([ContentLinks.apply]). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ContentLinksTest {

    private fun textView() = TextView(RuntimeEnvironment.getApplication())

    @Test
    fun strips_markers_and_shows_label() {
        val tv = textView()
        ContentLinks.apply(tv, "Use the [symptom checker](tool:symptom) when unsure.")
        assertThat(tv.text.toString()).isEqualTo("Use the symptom checker when unsure.")
    }

    @Test
    fun attaches_one_clickable_span_per_link() {
        val tv = textView()
        ContentLinks.apply(tv, "See [a](guide:x) and [b](tool:water).")
        val text = tv.text as Spanned
        assertThat(text.getSpans(0, text.length, ClickableSpan::class.java)).hasLength(2)
    }

    @Test
    fun plain_text_is_unchanged() {
        val tv = textView()
        ContentLinks.apply(tv, "No links here.")
        assertThat(tv.text.toString()).isEqualTo("No links here.")
    }

    /**
     * Cross-package contract, pinned at the owning site: [ContentLinks.apply] must
     * leave a *mutable* buffer behind. `TextView.setText` with the default
     * BufferType.NORMAL stores a Spanned as an immutable SpannedString, and the
     * guide renderer's hanging-indent pass then silently no-ops on its
     * `tv.text as? Spannable` cast (see GuideContentRendererTest, which fails with
     * this one). Both arms matter — the no-link path also hands `setText` a
     * SpannableStringBuilder, so it degrades to SpannedString just the same.
     */
    @Test
    fun leaves_a_mutable_spannable_buffer_for_later_span_passes() {
        val plain = textView()
        ContentLinks.apply(plain, "No links here.")
        assertThat(plain.text).isInstanceOf(Spannable::class.java)

        val linked = textView()
        ContentLinks.apply(linked, "See [a](guide:x).")
        assertThat(linked.text).isInstanceOf(Spannable::class.java)
    }
}
