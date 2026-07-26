package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadow.api.Shadow
import org.robolectric.shadows.ShadowNetworkCapabilities

/**
 * Robolectric coverage of the shared [ConnectivityObserver]: the
 * validated-internet rule the offline notices key off, the seeded snapshot, and
 * the default-network callback lifecycle (including the capability-change path
 * that a plain onAvailable/onLost watcher misses).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConnectivityObserverTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private val connectivityManager: ConnectivityManager
        get() = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    /** The shadow's default active network — always present in a unit test. */
    private val activeNetwork: Network get() = connectivityManager.activeNetwork!!

    private fun capabilities(vararg granted: Int): NetworkCapabilities {
        val caps = ShadowNetworkCapabilities.newInstance()
        val shadow = Shadow.extract<ShadowNetworkCapabilities>(caps)
        granted.forEach { shadow.addCapability(it) }
        return caps
    }

    private fun setActiveCapabilities(caps: NetworkCapabilities) {
        shadowOf(connectivityManager).setNetworkCapabilities(activeNetwork, caps)
    }

    /** Deliver a capability change to every registered default-network callback,
     *  the way the framework does when a network is (re)validated. */
    private fun fireCapabilitiesChanged(caps: NetworkCapabilities) {
        setActiveCapabilities(caps)
        shadowOf(connectivityManager).networkCallbacks.toList().forEach {
            it.onCapabilitiesChanged(activeNetwork, caps)
        }
    }

    private fun fireLost() {
        shadowOf(connectivityManager).networkCallbacks.toList().forEach { it.onLost(activeNetwork) }
    }

    private val online: NetworkCapabilities
        get() = capabilities(
            NetworkCapabilities.NET_CAPABILITY_INTERNET,
            NetworkCapabilities.NET_CAPABILITY_VALIDATED,
        )

    /**
     * Collect [ConnectivityObserver.isOnline] into [into], synchronously on the
     * test thread: with [Dispatchers.Unconfined] every emission — and the
     * `awaitClose` cleanup on cancel — runs inline, so no test scheduler is
     * needed (Robolectric delivers the network callbacks on this thread too).
     */
    private fun collectOnline(observer: ConnectivityObserver, into: MutableList<Boolean>): Job =
        CoroutineScope(Dispatchers.Unconfined).launch(start = CoroutineStart.UNDISPATCHED) {
            observer.isOnline.collect { into += it }
        }

    // ── The validated-internet rule ─────────────────────────────────────
    @Test
    fun offline_whenThereAreNoCapabilities() {
        assertThat(ConnectivityObserver.hasValidatedInternet(null)).isFalse()
        assertThat(ConnectivityObserver.hasValidatedInternet(capabilities())).isFalse()
    }

    @Test
    fun offline_whenInternetIsNotValidatedYet() {
        // A captive-portal / not-yet-validated network: our API calls fail on
        // it, so it must not read as online.
        val unvalidated = capabilities(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        assertThat(ConnectivityObserver.hasValidatedInternet(unvalidated)).isFalse()
    }

    @Test
    fun online_whenInternetIsValidated() {
        assertThat(ConnectivityObserver.hasValidatedInternet(online)).isTrue()
    }

    // ── Snapshot ────────────────────────────────────────────────────────
    @Test
    fun isOnlineNow_readsTheActiveNetwork() {
        val observer = ConnectivityObserver(context)
        setActiveCapabilities(capabilities(NetworkCapabilities.NET_CAPABILITY_INTERNET))
        assertThat(observer.isOnlineNow()).isFalse()

        setActiveCapabilities(online)
        assertThat(observer.isOnlineNow()).isTrue()
    }

    // ── The flow ────────────────────────────────────────────────────────
    @Test
    fun isOnline_seedsCurrentStateThenTracksChanges() {
        setActiveCapabilities(capabilities(NetworkCapabilities.NET_CAPABILITY_INTERNET))
        val observer = ConnectivityObserver(context)

        val seen = mutableListOf<Boolean>()
        val job = collectOnline(observer, seen)
        try {
            // Seeded from the current network, before any callback fires.
            assertThat(seen).containsExactly(false)

            // Validation completing fires onCapabilitiesChanged, NOT onAvailable —
            // the case that used to leave a banner stuck on screen.
            fireCapabilitiesChanged(online)
            assertThat(seen).containsExactly(false, true).inOrder()

            // Re-delivering the same state is collapsed (distinctUntilChanged).
            fireCapabilitiesChanged(online)
            assertThat(seen).containsExactly(false, true).inOrder()

            fireLost()
            assertThat(seen).containsExactly(false, true, false).inOrder()
        } finally {
            job.cancel()
        }
    }

    @Test
    fun isOnline_unregistersItsCallbackWhenCollectionStops() {
        val observer = ConnectivityObserver(context)
        val before = shadowOf(connectivityManager).networkCallbacks.size

        val job = collectOnline(observer, mutableListOf())
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before + 1)

        // Joining waits for the whole flow hierarchy, awaitClose block included.
        runBlocking { job.cancelAndJoin() }
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before)
    }
}
