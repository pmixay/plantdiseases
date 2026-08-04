package com.plantdiseases.app.ui.common

import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Covers the card's synchronous contract — the parts a host relies on before
 * any network enrichment lands. The Wikipedia enrichment path needs the Hilt
 * graph (for ScanRepository) and is deliberately not exercised here — this
 * test's host Activity is a plain (non-Hilt) `AppCompatActivity` — every case
 * below keeps `scientific` null so `loadEnrichment` is never reached.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SpeciesInfoCardViewTest {

    private fun card(): Pair<AppCompatActivity, SpeciesInfoCardView> {
        val activity = Robolectric.buildActivity(AppCompatActivity::class.java).setup().get()
        val view = SpeciesInfoCardView(activity)
        activity.setContentView(view)
        return activity to view
    }

    private val scope = CoroutineScope(Dispatchers.Unconfined)

    @Test
    fun hidesItself_whenNeitherNameIsUsable() {
        val (_, view) = card()

        assertThat(view.bind(scope, scientific = null, common = null)).isFalse()
        assertThat(view.visibility).isEqualTo(View.GONE)

        // Blank/whitespace must be treated the same as null, not rendered.
        assertThat(view.bind(scope, scientific = "   ", common = "")).isFalse()
        assertThat(view.visibility).isEqualTo(View.GONE)
    }

    @Test
    fun showsCommonName_butNoDetailAction_whenBinomialIsMissing() {
        val (_, view) = card()

        assertThat(view.bind(scope, scientific = null, common = "Monstera")).isTrue()

        assertThat(view.visibility).isEqualTo(View.VISIBLE)
        val name = view.findViewById<android.widget.TextView>(R.id.species_info_name)
        assertThat(name.text.toString()).isEqualTo("Monstera")
        // SpeciesDetailActivity finishes without a binomial, so the action that
        // would open it must not be offered.
        val more = view.findViewById<View>(R.id.species_info_more)
        assertThat(more.visibility).isEqualTo(View.GONE)
    }

    @Test
    fun startsCollapsed_withNoDescriptionAndNoToggle() {
        val (_, view) = card()

        view.bind(scope, scientific = null, common = "Monstera")

        assertThat(view.findViewById<View>(R.id.species_info_description).visibility)
            .isEqualTo(View.GONE)
        assertThat(view.findViewById<View>(R.id.species_info_expand_row).visibility)
            .isEqualTo(View.GONE)
    }

    @Test
    fun rebinding_clearsThePreviousSpecies() {
        val (_, view) = card()
        val name = view.findViewById<android.widget.TextView>(R.id.species_info_name)

        view.bind(scope, scientific = null, common = "Monstera")
        assertThat(name.text.toString()).isEqualTo("Monstera")

        view.bind(scope, scientific = null, common = "Ficus")
        assertThat(name.text.toString()).isEqualTo("Ficus")

        // A recycled host binding an unusable species must not keep showing the
        // last one.
        assertThat(view.bind(scope, scientific = null, common = null)).isFalse()
        assertThat(view.visibility).isEqualTo(View.GONE)
    }
}
