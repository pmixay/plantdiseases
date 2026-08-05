package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.shareIn
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * The app's single answer to "can we reach the server right now?".
 *
 * App-scoped (a Koin singleton — see `di/AppModule.kt`) and consumed by every
 * screen that fetches over the network, via the shared
 * [com.plantdiseases.app.ui.common.OfflineNoticeView].
 *
 * Android's VALIDATED capability is only the first gate. Some VPNs keep a
 * stale validated tunnel after their underlying Wi-Fi/mobile network is gone,
 * so this observer also probes PlantDiseases' lightweight health endpoint.
 * That makes the notice describe what the network-backed screens actually
 * need: current reachability of their server.
 */
class ConnectivityObserver internal constructor(
    context: Context,
    private val reachabilityProbe: suspend () -> Boolean,
    scope: CoroutineScope,
    probeTicks: Flow<Unit>,
    stopTimeoutMs: Long,
) {

    private val connectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    constructor(context: Context) : this(
        context = context,
        reachabilityProbe = productionReachabilityProbe(),
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
        probeTicks = periodicTicks(PROBE_INTERVAL_MS),
        stopTimeoutMs = STOP_TIMEOUT_MS,
    )

    /**
     * Shared online state. While at least one visible screen collects it there
     * is exactly one Android callback and one periodic server probe, regardless
     * of how many STARTED pager pages are observing.
     *
     * A missing/unvalidated Android network reports offline immediately. A
     * validated network must still pass the health probe; the probe repeats so
     * a stale VPN can be detected even when Android sends no capability event,
     * and recovery can be noticed without recreating the screen.
     */
    val isOnline: Flow<Boolean> = systemHasValidatedInternet()
        .combine(probeTicks.onStart { emit(Unit) }) { systemOnline, _ ->
            systemOnline
        }
        .map { systemOnline ->
            systemOnline && runCatching { reachabilityProbe() }.getOrDefault(false)
        }
        .catch { emit(false) }
        .distinctUntilChanged()
        .shareIn(
            scope = scope,
            started = SharingStarted.WhileSubscribed(stopTimeoutMillis = stopTimeoutMs),
            replay = 1,
        )

    private fun systemHasValidatedInternet(): Flow<Boolean> = callbackFlow {
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                trySend(hasValidatedInternet(caps))
            }

            override fun onLost(network: Network) {
                trySend(false)
            }

            override fun onUnavailable() {
                trySend(false)
            }
        }

        // Register before taking the snapshot so a network transition cannot
        // fall into the gap between those operations.
        try {
            connectivityManager.registerDefaultNetworkCallback(callback)
        } catch (_: RuntimeException) {
            trySend(false)
            close()
            return@callbackFlow
        }
        trySend(
            hasValidatedInternet(
                connectivityManager.getNetworkCapabilities(connectivityManager.activeNetwork),
            ),
        )
        awaitClose {
            runCatching { connectivityManager.unregisterNetworkCallback(callback) }
        }
    }.distinctUntilChanged()

    internal companion object {
        private const val PROBE_INTERVAL_MS = 15_000L
        private const val PROBE_TIMEOUT_MS = 3_000L
        private const val STOP_TIMEOUT_MS = 5_000L

        /** Fast first gate. A true result still requires the server probe. */
        internal fun hasValidatedInternet(caps: NetworkCapabilities?): Boolean =
            caps != null &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)

        private fun periodicTicks(intervalMs: Long): Flow<Unit> = flow {
            while (true) {
                kotlinx.coroutines.delay(intervalMs)
                emit(Unit)
            }
        }

        private fun productionReachabilityProbe(): suspend () -> Boolean {
            val client = OkHttpClient.Builder()
                .callTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
                .connectTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
                .readTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
                .retryOnConnectionFailure(false)
                .build()
            val request = Request.Builder()
                .url("${ServerConfig.baseUrl}api/health")
                .get()
                .build()

            return {
                withContext(Dispatchers.IO) {
                    runCatching {
                        client.newCall(request).execute().use { response ->
                            response.isSuccessful
                        }
                    }.getOrDefault(false)
                }
            }
        }
    }
}
