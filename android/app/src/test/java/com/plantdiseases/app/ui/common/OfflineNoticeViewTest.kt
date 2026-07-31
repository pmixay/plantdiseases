package com.plantdiseases.app.ui.common

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

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OfflineNoticeViewTest {

    @Test
    fun visibleOffline_hiddenOnline_whileOwnerIsStarted() {
        val controller = Robolectric.buildActivity(AppCompatActivity::class.java).setup()
        val activity = controller.get()
        val notice = OfflineNoticeView(activity)
        activity.setContentView(notice)
        val online = MutableStateFlow(true)

        notice.observe(activity, online)
        shadowOf(Looper.getMainLooper()).idle()
        assertThat(notice.visibility).isEqualTo(View.GONE)

        online.value = false
        shadowOf(Looper.getMainLooper()).idle()
        assertThat(notice.visibility).isEqualTo(View.VISIBLE)

        online.value = true
        shadowOf(Looper.getMainLooper()).idle()
        assertThat(notice.visibility).isEqualTo(View.GONE)

        controller.pause().stop().destroy()
    }
}
