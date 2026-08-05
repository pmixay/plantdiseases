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
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.launch
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadow.api.Shadow
import org.robolectric.shadows.ShadowNetworkCapabilities

/**
 * Robolectric coverage of the shared [ConnectivityObserver]: Android
 * capability gating, real server reachability (including a stale validated
 * VPN), periodic recovery, and the single callback's lifecycle.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConnectivityObserverTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private val connectivityManager: ConnectivityManager
        get() = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    /** The shadow's default active network — always present in this test. */
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

    private data class ObserverHarness(
        val observer: ConnectivityObserver,
        val scope: CoroutineScope,
    )

    private fun observer(
        probe: suspend () -> Boolean = { true },
        ticks: Flow<Unit> = emptyFlow(),
    ): ObserverHarness {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        return ObserverHarness(
            observer = ConnectivityObserver(
                context = context,
                reachabilityProbe = probe,
                scope = scope,
                probeTicks = ticks,
                stopTimeoutMs = 0,
            ),
            scope = scope,
        )
    }

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

    // ── The flow ────────────────────────────────────────────────────────
    @Test
    fun isOnline_gatesTheProbeThenTracksNetworkChanges() {
        setActiveCapabilities(capabilities(NetworkCapabilities.NET_CAPABILITY_INTERNET))
        var probeCalls = 0
        val harness = observer(
            probe = {
                probeCalls++
                true
            },
        )

        val seen = mutableListOf<Boolean>()
        val job = collectOnline(harness.observer, seen)
        try {
            // An unvalidated network is offline without wasting a server call.
            assertThat(seen).containsExactly(false)
            assertThat(probeCalls).isEqualTo(0)

            // Validation completing now permits the reachability probe.
            fireCapabilitiesChanged(online)
            assertThat(seen).containsExactly(false, true).inOrder()
            assertThat(probeCalls).isEqualTo(1)

            // Re-delivering identical capabilities is collapsed.
            fireCapabilitiesChanged(online)
            assertThat(seen).containsExactly(false, true).inOrder()
            assertThat(probeCalls).isEqualTo(1)

            fireLost()
            assertThat(seen).containsExactly(false, true, false).inOrder()
        } finally {
            job.cancel()
            harness.scope.cancel()
        }
    }

    @Test
    fun validatedVpn_isOfflineWhenTheServerCannotBeReached_thenRecoversOnTick() {
        // This is the real-device regression: Samsung still reported an
        // Amnezia VPN as INTERNET+VALIDATED after its underlying network was
        // gone. Capabilities alone would incorrectly hide every notice.
        setActiveCapabilities(online)
        var serverReachable = false
        val ticks = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
        val harness = observer(
            probe = { serverReachable },
            ticks = ticks,
        )

        val seen = mutableListOf<Boolean>()
        val job = collectOnline(harness.observer, seen)
        try {
            assertThat(seen).containsExactly(false)

            serverReachable = true
            assertThat(ticks.tryEmit(Unit)).isTrue()
            assertThat(seen).containsExactly(false, true).inOrder()

            serverReachable = false
            assertThat(ticks.tryEmit(Unit)).isTrue()
            assertThat(seen).containsExactly(false, true, false).inOrder()
        } finally {
            job.cancel()
            harness.scope.cancel()
        }
    }

    @Test
    fun isOnline_sharesOneCallbackAndUnregistersWhenCollectionStops() {
        setActiveCapabilities(online)
        val harness = observer()
        val before = shadowOf(connectivityManager).networkCallbacks.size

        val first = collectOnline(harness.observer, mutableListOf())
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before + 1)

        val second = collectOnline(harness.observer, mutableListOf())
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before + 1)

        first.cancel()
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before + 1)

        second.cancel()
        harness.scope.cancel()
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before)
    }
}
