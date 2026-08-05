package com.plantdiseases.app.util

import android.os.Looper
import android.widget.Button
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * The entry-point gate behind every "not available offline" affordance
 * (community, species ID, symptom triage, the AI doctor). A disabled View
 * swallows its touches without firing its click listener, so `isEnabled` is
 * the whole mechanism — this pins that it actually tracks reachability, and
 * that the dimming doesn't double up on controls that dim themselves.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConnectivityBindingTest {

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    @Test
    fun tracksReachability_bothWays() {
        val controller = Robolectric.buildActivity(AppCompatActivity::class.java).setup()
        val activity = controller.get()
        val tile = LinearLayout(activity)
        activity.setContentView(tile)
        val online = MutableStateFlow(true)

        activity.enableWhileOnline(online, tile)
        idle()
        assertThat(tile.isEnabled).isTrue()
        assertThat(tile.alpha).isEqualTo(1f)

        online.value = false
        idle()
        assertThat(tile.isEnabled).isFalse()
        assertThat(tile.alpha).isLessThan(1f)

        online.value = true
        idle()
        assertThat(tile.isEnabled).isTrue()
        assertThat(tile.alpha).isEqualTo(1f)

        controller.pause().stop().destroy()
    }

    @Test
    fun containersAreDimmed_butButtonsAreLeftToMaterial() {
        val controller = Robolectric.buildActivity(AppCompatActivity::class.java).setup()
        val activity = controller.get()
        val card = LinearLayout(activity)
        val button = Button(activity)
        activity.setContentView(LinearLayout(activity).apply { addView(card) })
        val online = MutableStateFlow(false)

        activity.enableWhileOnline(online, card, button)
        idle()

        assertThat(card.isEnabled).isFalse()
        assertThat(card.alpha).isLessThan(1f)
        assertThat(button.isEnabled).isFalse()
        assertThat(button.alpha).isEqualTo(1f)

        controller.pause().stop().destroy()
    }
}
