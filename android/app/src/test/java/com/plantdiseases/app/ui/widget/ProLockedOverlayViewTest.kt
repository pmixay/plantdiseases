package com.plantdiseases.app.ui.widget

import android.content.Context
import android.view.View
import androidx.appcompat.view.ContextThemeWrapper
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ProLockedOverlayViewTest {
    private val context: Context
        get() = ContextThemeWrapper(
            ApplicationProvider.getApplicationContext(),
            R.style.Theme_PlantDiseases,
        )

    @Test
    fun overlayContainerOwnsBackgroundTapWithoutACompetingChildTarget() {
        val overlay = ProLockedOverlayView(context)
        val preview = View(context)
        var unlockCalls = 0

        overlay.bindPreview(preview, locked = true) { unlockCalls++ }

        val inflatedChild = overlay.getChildAt(0)
        assertThat(inflatedChild.isClickable).isFalse()
        assertThat(inflatedChild.isFocusable).isFalse()
        assertThat(overlay.isClickable).isTrue()
        assertThat(overlay.isFocusable).isTrue()
        assertThat(overlay.performClick()).isTrue()
        assertThat(unlockCalls).isEqualTo(1)
    }
}
