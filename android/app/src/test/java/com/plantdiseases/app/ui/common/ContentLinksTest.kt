package com.plantdiseases.app.ui.common

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
}
