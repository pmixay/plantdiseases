package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.distinctUntilChanged

/**
 * The app's single answer to "can we reach the server right now?".
 *
 * App-scoped (a Koin singleton — see `di/AppModule.kt`) and consumed by every
 * screen that fetches over the network, via the shared
 * [com.plantdiseases.app.ui.common.OfflineNoticeView].
 *
 * It reports *reachability*, not "a network object exists": a hotel Wi-Fi the
 * system hasn't validated counts as offline, because our API calls would fail
 * on it anyway.
 */
class ConnectivityObserver(context: Context) {

    private val connectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    /**
     * Online state: seeded with the current value the moment it's collected,
     * then updated on every default-network change.
     *
     * Cold — each collector registers its own default-network callback and
     * unregisters it when collection ends, so nothing is held while no screen
     * is listening.
     *
     * Capability changes are tracked as well as loss, because a reconnect (or
     * the system finishing internet validation on a network that was already
     * "available") only fires `onCapabilitiesChanged`; watching availability
     * alone is what used to leave the camera's banner stuck until a restart.
     */
    val isOnline: Flow<Boolean> = callbackFlow {
        trySend(isOnlineNow())
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
        connectivityManager.registerDefaultNetworkCallback(callback)
        awaitClose { connectivityManager.unregisterNetworkCallback(callback) }
    }.distinctUntilChanged()

    /** One-shot snapshot, for a decision that can't wait for an emission. */
    fun isOnlineNow(): Boolean = hasValidatedInternet(
        connectivityManager.getNetworkCapabilities(connectivityManager.activeNetwork),
    )

    internal companion object {
        /** True only when the network can actually reach the internet — present
         *  AND validated by the system. */
        internal fun hasValidatedInternet(caps: NetworkCapabilities?): Boolean =
            caps != null &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }
}
