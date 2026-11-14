package com.plantdiseases.app.ui.common

import android.app.Application
import android.os.Looper
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class OfflineNoticeViewTest {

    /** Past the strip's show-delay, so a settled offline state has painted. */
    private fun settle(millis: Long = 1_000) {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(millis))
    }

    @Test
    fun visibleOffline_hiddenOnline_whileOwnerIsStarted() {
        val controller = Robolectric.buildActivity(AppCompatActivity::class.java).setup()
        val activity = controller.get()
        val notice = OfflineNoticeView(activity)
        activity.setContentView(notice)
        val online = MutableStateFlow(true)

        notice.observe(activity, online)
        settle()
        assertThat(notice.visibility).isEqualTo(View.GONE)

        online.value = false
        settle()
        assertThat(notice.visibility).isEqualTo(View.VISIBLE)

        online.value = true
        shadowOf(Looper.getMainLooper()).idle()
        assertThat(notice.visibility).isEqualTo(View.GONE)

        controller.pause().stop().destroy()
    }

    @Test
    fun transientOffline_neverPaints() {
        // The foreground-return case: the shared flow replays its last value to
        // a new subscriber a beat before the re-seeded state lands. A strip
        // that painted on that replayed value is the "offline banner on an
        // online phone" bug — it must wait for the state to hold.
        val controller = Robolectric.buildActivity(AppCompatActivity::class.java).setup()
        val activity = controller.get()
        val notice = OfflineNoticeView(activity)
        activity.setContentView(notice)
        val online = MutableStateFlow(false)

        notice.observe(activity, online)
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(100))
        assertThat(notice.visibility).isEqualTo(View.GONE)

        online.value = true
        settle()
        assertThat(notice.visibility).isEqualTo(View.GONE)

        controller.pause().stop().destroy()
    }
}
