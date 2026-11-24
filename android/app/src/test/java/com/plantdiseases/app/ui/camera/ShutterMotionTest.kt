package com.plantdiseases.app.ui.camera

import android.app.Application
import android.content.Context
import android.graphics.drawable.AnimatedVectorDrawable
import android.widget.ImageView
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class ShutterMotionTest {

    private val context: Context = ApplicationProvider.getApplicationContext()
    private val button = ImageView(context).apply { setImageResource(R.drawable.avd_shutter_iris) }

    @Test
    fun shutterDrawableIsTheIrisAnimation() {
        assertThat(button.drawable).isInstanceOf(AnimatedVectorDrawable::class.java)
    }

    @Test
    fun busyLocksTheShutterWithoutAnyBusyIndicator() {
        val motion = ShutterMotion(button, animationsEnabled = { true })

        motion.setBusy(true)

        assertThat(button.isEnabled).isFalse()
        assertThat(motion.isBusy).isTrue()
        // No dimming either: nothing shows a loading state before the cropper.
        assertThat(button.imageAlpha).isEqualTo(255)
    }

    @Test
    fun rearmingRestoresTheShutter() {
        val motion = ShutterMotion(button, animationsEnabled = { true })
        motion.setBusy(true)

        motion.setBusy(false)

        assertThat(button.isEnabled).isTrue()
        assertThat(motion.isBusy).isFalse()
    }

    @Test
    fun rearmingAnIdleShutterIsHarmless() {
        val motion = ShutterMotion(button, animationsEnabled = { true })

        motion.setBusy(false)

        assertThat(button.isEnabled).isTrue()
    }

    @Test
    fun withAnimationsOffTheClickIsSkipped() {
        val motion = ShutterMotion(button, animationsEnabled = { false })

        motion.click()

        assertThat(button.scaleX).isEqualTo(1f)
    }
}
