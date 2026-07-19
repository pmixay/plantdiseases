package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowNetworkCapabilities

/**
 * The app-wide connectivity signal: seeding from the current default network,
 * the INTERNET+VALIDATED rule, and the callback transitions that used to get
 * the old per-screen banner stuck (validation arriving via
 * onCapabilitiesChanged rather than onAvailable).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConnectivityMonitorTest {

    private lateinit var context: Context
    private lateinit var cm: ConnectivityManager

    @Before
    fun setup() {
        ConnectivityMonitor.resetForTest()
        context = ApplicationProvider.getApplicationContext()
        cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    }

    private fun caps(vararg capabilities: Int): NetworkCapabilities {
        val caps = ShadowNetworkCapabilities.newInstance()
        capabilities.forEach { shadowOf(caps).addCapability(it) }
        return caps
    }

    private fun validatedCaps() = caps(
        NetworkCapabilities.NET_CAPABILITY_INTERNET,
        NetworkCapabilities.NET_CAPABILITY_VALIDATED,
    )

    /** The monitor's callback, as registered on the system service. */
    private fun registeredCallback(): ConnectivityManager.NetworkCallback =
        shadowOf(cm).networkCallbacks.single()

    @Test
    fun start_seedsOnline_fromValidatedDefaultNetwork() {
        shadowOf(cm).setNetworkCapabilities(cm.activeNetwork, validatedCaps())
        ConnectivityMonitor.start(context)
        assertThat(ConnectivityMonitor.isOnline.value).isTrue()
    }

    @Test
    fun start_seedsOffline_whenInternetIsNotValidated() {
        shadowOf(cm).setNetworkCapabilities(
            cm.activeNetwork,
            caps(NetworkCapabilities.NET_CAPABILITY_INTERNET),
        )
        ConnectivityMonitor.start(context)
        assertThat(ConnectivityMonitor.isOnline.value).isFalse()
    }

    @Test
    fun onLost_flipsOffline_and_revalidation_flipsBackOnline() {
        shadowOf(cm).setNetworkCapabilities(cm.activeNetwork, validatedCaps())
        ConnectivityMonitor.start(context)
        val callback = registeredCallback()
        val network = cm.activeNetwork!!

        callback.onLost(network)
        assertThat(ConnectivityMonitor.isOnline.value).isFalse()

        // Reconnecting delivers onCapabilitiesChanged, not onAvailable — the
        // transition the old per-screen banner missed.
        callback.onCapabilitiesChanged(network, validatedCaps())
        assertThat(ConnectivityMonitor.isOnline.value).isTrue()
    }

    @Test
    fun start_isIdempotent_registersOneCallback() {
        ConnectivityMonitor.start(context)
        ConnectivityMonitor.start(context)
        assertThat(shadowOf(cm).networkCallbacks).hasSize(1)
    }

    @Test
    fun isOnlineNow_startsAndAnswersFromCurrentState() {
        shadowOf(cm).setNetworkCapabilities(cm.activeNetwork, validatedCaps())
        assertThat(ConnectivityMonitor.isOnlineNow(context)).isTrue()
    }

    @Test
    fun hasUsableInternet_requiresPresentValidatedInternet() {
        assertThat(ConnectivityMonitor.hasUsableInternet(null)).isFalse()
        assertThat(
            ConnectivityMonitor.hasUsableInternet(
                caps(NetworkCapabilities.NET_CAPABILITY_INTERNET),
            ),
        ).isFalse()
        assertThat(ConnectivityMonitor.hasUsableInternet(validatedCaps())).isTrue()
    }
}
