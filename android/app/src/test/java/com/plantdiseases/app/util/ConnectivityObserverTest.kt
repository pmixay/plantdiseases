package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadow.api.Shadow
import org.robolectric.shadows.ShadowNetworkCapabilities

/**
 * Robolectric coverage of the shared [ConnectivityObserver]: the system
 * capability gate, network handovers, the reports-driven reachability gate
 * (including a stale validated VPN), recovery, and — the point of the whole
 * design — that an online device is never polled.
 *
 * Virtual time throughout: the observer waits out probe retries and recovery
 * backoff, so the tests drive a [TestScope] clock rather than sleeping. Note
 * that `advanceUntilIdle` is only safe while the observer believes it is
 * online; offline it runs a backoff loop that never goes idle, so those cases
 * step the clock explicitly.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConnectivityObserverTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private val connectivityManager: ConnectivityManager
        get() = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    private val activeNetwork get() = connectivityManager.activeNetwork!!

    private fun capabilities(vararg granted: Int): NetworkCapabilities {
        val caps = ShadowNetworkCapabilities.newInstance()
        val shadow = Shadow.extract<ShadowNetworkCapabilities>(caps)
        granted.forEach { shadow.addCapability(it) }
        return caps
    }

    private val online: NetworkCapabilities
        get() = capabilities(
            NetworkCapabilities.NET_CAPABILITY_INTERNET,
            NetworkCapabilities.NET_CAPABILITY_VALIDATED,
        )

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

    private fun TestScope.observer(
        probe: suspend () -> Boolean,
        reports: MutableSharedFlow<Boolean> = MutableSharedFlow(extraBufferCapacity = 8),
        scope: CoroutineScope = backgroundScope,
    ) = ConnectivityObserver(
        context = context,
        probe = probe,
        reports = reports,
        scope = scope,
        nowMs = { currentTime },
        stopTimeoutMs = 0,
    )

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

    @Test
    fun recoveryBackoff_growsThenCaps() {
        val delays = (0..6).map { ConnectivityObserver.recoveryDelayMs(it) }
        assertThat(delays).containsExactly(3_000L, 6_000L, 12_000L, 24_000L, 30_000L, 30_000L, 30_000L)
            .inOrder()
    }

    // ── Gate 1: the system's view ───────────────────────────────────────
    @Test
    fun unvalidatedNetwork_isOfflineWithoutSpendingAProbe() = runTest {
        setActiveCapabilities(capabilities(NetworkCapabilities.NET_CAPABILITY_INTERNET))
        var probeCalls = 0
        val observer = observer(
            probe = {
                probeCalls++
                true
            },
        )

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceUntilIdle()

        assertThat(seen).containsExactly(false)
        assertThat(probeCalls).isEqualTo(0)

        // Validation completing now permits the reachability gate to run.
        fireCapabilitiesChanged(online)
        advanceUntilIdle()
        assertThat(seen).containsExactly(false, true).inOrder()
    }

    @Test
    fun handover_doesNotReportOfflineWhileAnotherDefaultNetworkIsUp() = runTest {
        // The regression this guards: `onLost` for the network being handed
        // off from used to be trusted as-is, so every Wi-Fi→mobile switch (and
        // every VPN connect) blinked the notice even though a validated
        // default network was already in place.
        setActiveCapabilities(online)
        val observer = observer(probe = { true })

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceUntilIdle()
        assertThat(seen).containsExactly(true)

        fireLost() // old network gone, the new default is already validated
        advanceUntilIdle()
        assertThat(seen).containsExactly(true)
    }

    @Test
    fun losingTheLastNetwork_reportsOfflineImmediately() = runTest {
        setActiveCapabilities(online)
        val observer = observer(probe = { true })

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceUntilIdle()

        setActiveCapabilities(capabilities()) // nothing left to fall back to
        fireLost()
        advanceUntilIdle()
        assertThat(seen).containsExactly(true, false).inOrder()
    }

    // ── Gate 2: can this network actually reach us? ─────────────────────
    @Test
    fun validatedVpn_isOfflineWhenTheServerCannotBeReached() = runTest {
        // The real-device regression: Samsung still reported an Amnezia VPN as
        // INTERNET+VALIDATED after its underlying network was gone.
        setActiveCapabilities(online)
        var probeCalls = 0
        val observer = observer(
            probe = {
                probeCalls++
                false
            },
        )

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceTimeBy(1_000)
        runCurrent()

        // Starts optimistic, then the confirming probe settles it. Two
        // attempts, so one slow round-trip on a waking tunnel is not enough
        // to claim the device is offline.
        assertThat(seen).containsExactly(true, false).inOrder()
        assertThat(probeCalls).isEqualTo(2)
    }

    @Test
    fun onlineDevice_isNeverPolled() = runTest {
        // The whole point of the reports-driven design: a reachable device
        // costs exactly one probe — the initial read — no matter how long a
        // screen stays open.
        setActiveCapabilities(online)
        var probeCalls = 0
        val observer = observer(
            probe = {
                probeCalls++
                true
            },
        )

        backgroundScope.launch { observer.isOnline.toList(mutableListOf()) }
        advanceUntilIdle()
        assertThat(probeCalls).isEqualTo(1)

        advanceTimeBy(10 * 60_000)
        runCurrent()
        assertThat(probeCalls).isEqualTo(1)
    }

    @Test
    fun reportedFailure_flipsOfflineOnlyOnceAProbeAgrees() = runTest {
        setActiveCapabilities(online)
        var reachable = true
        val reports = MutableSharedFlow<Boolean>(extraBufferCapacity = 8)
        val observer = observer(probe = { reachable }, reports = reports)

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceUntilIdle()
        assertThat(seen).containsExactly(true)

        // A one-off transport failure (a request dying on a handover, an
        // upload timing out) must not paint the screen offline.
        reports.emit(false)
        advanceUntilIdle()
        assertThat(seen).containsExactly(true)

        // A failure the probe corroborates does.
        reachable = false
        reports.emit(false)
        advanceTimeBy(1_000)
        runCurrent()
        assertThat(seen).containsExactly(true, false).inOrder()
    }

    @Test
    fun recovery_isNoticedByASuccessfulRequestWithoutProbing() = runTest {
        setActiveCapabilities(online)
        var probeCalls = 0
        val reports = MutableSharedFlow<Boolean>(extraBufferCapacity = 8)
        val observer = observer(
            probe = {
                probeCalls++
                false
            },
            reports = reports,
        )

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceTimeBy(1_000)
        runCurrent()
        assertThat(seen).containsExactly(true, false).inOrder()

        val probesWhileOffline = probeCalls
        reports.emit(true)
        runCurrent()
        assertThat(seen).containsExactly(true, false, true).inOrder()
        assertThat(probeCalls).isEqualTo(probesWhileOffline)
    }

    @Test
    fun recovery_isNoticedByTheBackoffProbeWhenNoRequestsAreMade() = runTest {
        setActiveCapabilities(online)
        var reachable = false
        val observer = observer(probe = { reachable })

        val seen = mutableListOf<Boolean>()
        backgroundScope.launch { observer.isOnline.toList(seen) }
        advanceTimeBy(1_000)
        runCurrent()
        assertThat(seen).containsExactly(true, false).inOrder()

        reachable = true
        advanceTimeBy(ConnectivityObserver.recoveryDelayMs(0) + 100)
        runCurrent()
        assertThat(seen).containsExactly(true, false, true).inOrder()
    }

    // ── One callback for the whole app ──────────────────────────────────
    @Test
    fun isOnline_sharesOneCallbackAndUnregistersWhenCollectionStops() = runTest {
        setActiveCapabilities(online)
        val observer = observer(probe = { true })
        val before = shadowOf(connectivityManager).networkCallbacks.size

        val first = backgroundScope.launch { observer.isOnline.toList(mutableListOf()) }
        val second = backgroundScope.launch { observer.isOnline.toList(mutableListOf()) }
        advanceUntilIdle()
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before + 1)

        first.cancel()
        advanceUntilIdle()
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before + 1)

        second.cancel()
        advanceUntilIdle()
        assertThat(shadowOf(connectivityManager).networkCallbacks).hasSize(before)
    }
}
