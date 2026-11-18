package com.plantdiseases.app.ui.camera

import android.app.Application
import android.content.Context
import android.graphics.drawable.AnimatedVectorDrawable
import android.view.View
import android.widget.ImageView
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowLooper

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class ShutterMotionTest {

    private val context: Context = ApplicationProvider.getApplicationContext()
    private val button = ImageView(context).apply { setImageResource(R.drawable.avd_shutter_iris) }
    private val arc = View(context).apply { visibility = View.GONE }

    @Test
    fun shutterDrawableIsTheIrisAnimation() {
        assertThat(button.drawable).isInstanceOf(AnimatedVectorDrawable::class.java)
    }

    @Test
    fun busyDisablesTheShutterDimsTheRingAndSpinsTheArc() {
        val motion = ShutterMotion(button, arc, animationsEnabled = { true })

        motion.setBusy(true)

        assertThat(button.isEnabled).isFalse()
        assertThat(button.imageAlpha).isLessThan(255)
        assertThat(arc.visibility).isEqualTo(View.VISIBLE)
        assertThat(motion.isSpinning).isTrue()
    }

    @Test
    fun rearmingRestoresTheShutterAndHidesTheArc() {
        val motion = ShutterMotion(button, arc, animationsEnabled = { true })
        motion.setBusy(true)

        motion.setBusy(false)
        ShadowLooper.idleMainLooper(1, java.util.concurrent.TimeUnit.SECONDS)

        assertThat(button.isEnabled).isTrue()
        assertThat(button.imageAlpha).isEqualTo(255)
        assertThat(motion.isSpinning).isFalse()
        assertThat(arc.visibility).isEqualTo(View.GONE)
    }

    @Test
    fun rearmingAnIdleShutterIsHarmless() {
        val motion = ShutterMotion(button, arc, animationsEnabled = { true })

        motion.setBusy(false)

        assertThat(button.isEnabled).isTrue()
        assertThat(arc.visibility).isEqualTo(View.GONE)
    }

    @Test
    fun withAnimationsOffTheArcStaysStillAndHidesAtOnce() {
        val motion = ShutterMotion(button, arc, animationsEnabled = { false })

        motion.click()
        motion.setBusy(true)

        assertThat(button.scaleX).isEqualTo(1f)
        assertThat(arc.visibility).isEqualTo(View.VISIBLE)
        assertThat(motion.isSpinning).isFalse()

        motion.setBusy(false)

        assertThat(arc.visibility).isEqualTo(View.GONE)
        assertThat(button.isEnabled).isTrue()
    }

    @Test
    fun releaseStopsTheSpin() {
        val motion = ShutterMotion(button, arc, animationsEnabled = { true })
        motion.setBusy(true)

        motion.release()

        assertThat(motion.isSpinning).isFalse()
    }
}
