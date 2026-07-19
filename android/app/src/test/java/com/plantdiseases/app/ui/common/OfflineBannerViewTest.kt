package com.plantdiseases.app.ui.common

import android.app.Activity
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Looper
import android.view.View
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.util.ConnectivityMonitor
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowNetworkCapabilities

/**
 * The self-driving offline banner: visible exactly while the shared
 * connectivity signal says offline AND the host screen hasn't suppressed it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OfflineBannerViewTest {

    private lateinit var cm: ConnectivityManager
    private lateinit var activity: Activity
    private lateinit var banner: OfflineBannerView

    @Before
    fun setup() {
        ConnectivityMonitor.resetForTest()
        val context = ApplicationProvider.getApplicationContext<Context>()
        cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        // Pin the default network to "present but not validated" so every
        // test starts from a deterministic offline seed.
        shadowOf(cm).setNetworkCapabilities(
            cm.activeNetwork,
            ShadowNetworkCapabilities.newInstance(),
        )
        activity = Robolectric.buildActivity(Activity::class.java).setup().get()
        banner = OfflineBannerView(activity)
    }

    private fun validatedCaps(): NetworkCapabilities {
        val caps = ShadowNetworkCapabilities.newInstance()
        shadowOf(caps).addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        shadowOf(caps).addCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        return caps
    }

    private fun attach() {
        activity.setContentView(banner)
        shadowOf(Looper.getMainLooper()).idle()
    }

    @Test
    fun showsWhileOffline_hidesWhenConnectivityReturns() {
        // No validated caps on the default network → seeded offline.
        attach()
        assertThat(banner.visibility).isEqualTo(View.VISIBLE)

        val callback = shadowOf(cm).networkCallbacks.single()
        callback.onCapabilitiesChanged(cm.activeNetwork!!, validatedCaps())
        shadowOf(Looper.getMainLooper()).idle()
        assertThat(banner.visibility).isEqualTo(View.GONE)

        callback.onLost(cm.activeNetwork!!)
        shadowOf(Looper.getMainLooper()).idle()
        assertThat(banner.visibility).isEqualTo(View.VISIBLE)
    }

    @Test
    fun suppressed_hidesEvenWhileOffline() {
        attach()
        assertThat(banner.visibility).isEqualTo(View.VISIBLE)

        banner.suppressed = true
        assertThat(banner.visibility).isEqualTo(View.GONE)

        banner.suppressed = false
        assertThat(banner.visibility).isEqualTo(View.VISIBLE)
    }
}
