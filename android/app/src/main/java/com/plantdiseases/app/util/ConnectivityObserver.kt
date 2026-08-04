package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.shareIn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * The app's single answer to "can we reach the server right now?".
 *
 * App-scoped (a Koin singleton — see `di/AppModule.kt`) and consumed by every
 * screen that either shows the shared
 * [com.plantdiseases.app.ui.common.OfflineNoticeView] or disables an
 * online-only action (see `util/ConnectivityBinding.kt`).
 *
 * ## How the state is decided
 *
 * Two gates, in order:
 *
 * 1. **The system gate.** Android's INTERNET+VALIDATED capability on the
 *    *current default network*. A missing or unvalidated network (airplane
 *    mode, captive portal) is offline immediately and costs nothing. Every
 *    callback re-reads the live default network rather than trusting the
 *    event: during a Wi-Fi→mobile handover — or when a VPN connects — the
 *    framework reports `onLost` for the old network *after* the new one is
 *    already the default, and trusting that event blindly is what made the
 *    notice blink on every network switch.
 *
 * 2. **Server reachability.** Some VPNs keep a stale VALIDATED tunnel after
 *    their underlying network is gone, so a validated network still has to be
 *    shown to actually carry traffic to our server. This is driven by
 *    [ReachabilityReporter] — the outcome of requests the app is making
 *    anyway — and only falls back to an explicit probe when there is nothing
 *    to learn from: to confirm a reported failure, and to notice recovery
 *    while offline. **While online, nothing is polled at all.**
 *
 * ## Why it doesn't lie on the way back from the background
 *
 * Sharing stops a few seconds after the last screen stops, and restarts when
 * one comes back. A restart always starts **optimistic** and lets the gates
 * correct it — it never resumes a stale offline verdict. The system gate
 * answers instantly, so a genuinely disconnected phone is caught before the
 * strip could paint anyway; the reachability gate re-probes unless it was
 * demonstrably reaching us seconds ago.
 *
 * That ordering is deliberate. A wrongly-shown offline strip is far more
 * damaging than one that appears a second late, and the strip itself only
 * paints once the state has held (see `OfflineNoticeView`), so neither the
 * shared flow's replayed value nor a transient blip ever reaches the screen.
 */
class ConnectivityObserver(
    context: Context,
    /** One reachability check against the server. See `PlantApiClient.probeReachable`. */
    private val probe: suspend () -> Boolean,
    /** Outcomes of real API calls — see [ReachabilityReporter]. */
    private val reports: Flow<Boolean>,
    scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
    private val nowMs: () -> Long = SystemClock::elapsedRealtime,
    stopTimeoutMs: Long = STOP_TIMEOUT_MS,
) {

    private val connectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    /** Last state published to screens — system gate included. Read by
     *  [lastKnownOnline]; never used to seed a restart. */
    @Volatile
    private var lastPublished: Boolean? = null

    /**
     * Last *reachability* reading and when it was taken — the seed for a
     * restarted driver.
     *
     * Deliberately separate from [lastPublished]: an offline reading that came
     * from the system gate ("this device has no validated network") carries no
     * information about whether the server would answer, so seeding a
     * reachability driver with it would strand a freshly-connected network in
     * the offline backoff loop instead of probing it.
     */
    @Volatile
    private var lastReachability: Boolean? = null

    @Volatile
    private var lastReachabilityAt = 0L

    /**
     * Shared online state. While at least one visible screen collects it there
     * is exactly one Android callback and one reachability driver, regardless
     * of how many STARTED screens are observing; when the last one stops, both
     * are torn down.
     */
    @OptIn(ExperimentalCoroutinesApi::class)
    val isOnline: Flow<Boolean> = systemValidatedInternet()
        .flatMapLatest { systemOnline ->
            if (systemOnline) serverReachability() else flowOf(false)
        }
        // A broken observer must never fabricate an offline app: if this flow
        // fails, say online and let the screens' own error handling speak.
        .catch { emit(true) }
        .distinctUntilChanged()
        .onEach { lastPublished = it }
        .shareIn(
            scope = scope,
            started = SharingStarted.WhileSubscribed(stopTimeoutMillis = stopTimeoutMs),
            replay = 1,
        )

    /**
     * The last state [isOnline] published, for the handful of one-shot
     * decisions that can't wait on a flow — a tap handler asking "can this run
     * right now", a bottom sheet rendering rows once.
     *
     * Only meaningful while some screen is observing [isOnline]; with nothing
     * ever collected it answers optimistically, same as the flow's own seed.
     * Prefer [isOnline] anywhere the answer should keep updating.
     */
    val lastKnownOnline: Boolean get() = lastPublished ?: OPTIMISTIC

    // ── Gate 1: the system's view of the default network ────────────────
    private fun systemValidatedInternet(): Flow<Boolean> = callbackFlow {
        // Always answer from the *live* default network, never from the event
        // payload — see the class doc on handovers.
        fun publish() {
            trySend(
                hasValidatedInternet(
                    runCatching {
                        connectivityManager.getNetworkCapabilities(connectivityManager.activeNetwork)
                    }.getOrNull(),
                ),
            )
        }

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = publish()
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) = publish()
            override fun onLost(network: Network) = publish()
        }

        // Register before taking the first snapshot so a network transition
        // cannot fall into the gap between those operations.
        try {
            connectivityManager.registerDefaultNetworkCallback(callback)
        } catch (_: RuntimeException) {
            // No callback means no signal, not an offline device. Hand the
            // reachability driver an optimistic gate and let it probe.
            trySend(true)
            close()
            return@callbackFlow
        }
        publish()
        awaitClose {
            runCatching { connectivityManager.unregisterNetworkCallback(callback) }
        }
    }.distinctUntilChanged()

    // ── Gate 2: can this validated network actually reach our server? ────
    private fun serverReachability(): Flow<Boolean> = channelFlow {
        // Start optimistic and skip the initial probe only when we were
        // demonstrably reachable moments ago — the quick tab switch / short
        // background trip. Anything else earns a fresh probe.
        val recentlyReachable = wasReachableRecently()
        val state = MutableStateFlow(OPTIMISTIC)

        // Record every reading, not just the ones that change the state: the
        // timestamp is what decides whether a restart can re-seed from it.
        fun settle(reachable: Boolean) {
            remember(reachable)
            state.value = reachable
        }

        // Real API traffic is the primary signal and costs nothing.
        launch {
            reports.collect { reachable ->
                if (reachable) {
                    settle(true)
                } else if (state.value) {
                    // One transport failure is not proof — a request can die
                    // on a handover, or time out on a slow upload, while the
                    // server is perfectly reachable. Confirm before flipping.
                    settle(probeConfirming())
                }
            }
        }

        // Probes: the initial read, then recovery polling — and the polling
        // runs only while we believe we're offline. An online device never
        // reaches the `else` branch, which is the whole point: no timer, no
        // traffic, nothing to drain.
        launch {
            if (!recentlyReachable) settle(probeConfirming())
            var attempt = 0
            while (isActive) {
                if (state.value) {
                    state.first { !it } // suspends indefinitely — zero cost
                    attempt = 0
                } else {
                    delay(recoveryDelayMs(attempt))
                    // A real request may have recovered us during the wait.
                    if (state.value) continue
                    settle(probeOnce())
                    if (!state.value) attempt++
                }
            }
        }

        state.collect { send(it) }
    }

    /**
     * A probe that has to be wrong twice to report unreachable. The single
     * attempt this replaced turned one slow round-trip — routine on a VPN
     * tunnel that has just woken up — into a full offline claim.
     */
    private suspend fun probeConfirming(): Boolean {
        if (probeOnce()) return true
        delay(PROBE_RETRY_GAP_MS)
        return probeOnce()
    }

    /** A probe that can't take the observer down with it. A [probe] that threw
     *  would fail the shared flow, stranding every screen on its last value. */
    private suspend fun probeOnce(): Boolean = try {
        probe()
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        false
    }

    private fun remember(reachable: Boolean) {
        lastReachability = reachable
        lastReachabilityAt = nowMs()
    }

    /**
     * Were we demonstrably reaching the server a moment ago?
     *
     * Only a recent *success* counts. Re-seeding from a recent failure would
     * skip the initial probe at precisely the moment it matters most — a
     * screen coming back right after the user turned Wi-Fi on — leaving the
     * strip up until the backoff timer got around to noticing.
     */
    private fun wasReachableRecently(): Boolean =
        lastReachability == true && nowMs() - lastReachabilityAt <= OBSERVATION_TTL_MS

    internal companion object {
        /**
         * What an unseeded reachability driver assumes until a probe answers.
         * Optimistic on purpose: showing "offline" to an online user is a
         * visible lie, while the reverse is invisible — the next request
         * corrects it.
         */
        private const val OPTIMISTIC = true

        /** How long a published state stays good enough to re-seed a restart. */
        private const val OBSERVATION_TTL_MS = 20_000L

        /** Pause between the two attempts of a confirming probe. */
        private const val PROBE_RETRY_GAP_MS = 700L

        /** Sharing keep-alive across a config change / quick tab switch. */
        private const val STOP_TIMEOUT_MS = 5_000L

        /** Recovery probing while offline: 3s, 6s, 12s, 24s, then every 30s. */
        internal fun recoveryDelayMs(attempt: Int): Long =
            (3_000L shl attempt.coerceIn(0, 5)).coerceAtMost(30_000L)

        /** Fast first gate. A true result still requires gate 2. */
        internal fun hasValidatedInternet(caps: NetworkCapabilities?): Boolean =
            caps != null &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }
}
