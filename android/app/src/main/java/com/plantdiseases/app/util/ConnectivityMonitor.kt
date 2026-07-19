package com.plantdiseases.app.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import androidx.annotation.VisibleForTesting
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * App-wide connectivity signal — the single source of truth for "are we
 * online?".
 *
 * Every screen used to answer this on its own: the Scan tab registered a
 * private [ConnectivityManager.NetworkCallback] for its banner while the rest
 * of the app discovered offline state reactively (a request failed, a card
 * silently rendered blank). This object registers ONE default-network callback
 * for the whole process and exposes the state as a [StateFlow], so any screen
 * can show a consistent "offline — showing cached content" affordance instead
 * of a blank placeholder (see `ui/common/OfflineBannerView`).
 *
 * "Online" means the default network reports INTERNET *and* VALIDATED — real
 * reachability as judged by the system, not just "a network object exists".
 * The callback tracks capability *changes*, not only onAvailable/onLost:
 * reconnecting (or the system re-validating a network that was already
 * "available") fires onCapabilitiesChanged, and listening to onAvailable alone
 * is how the old Scan banner used to get stuck.
 *
 * The callback is registered once, with the application context, and is never
 * unregistered — process-lifetime state, like the system service itself.
 */
object ConnectivityMonitor {

    private val _isOnline = MutableStateFlow(true)

    /** Latest connectivity state. Seeded on [start]; defaults to online so
     *  nothing flashes an offline banner before the first real reading. */
    val isOnline: StateFlow<Boolean> get() = _isOnline

    private var started = false

    /** Idempotent. Seeds the state synchronously from the current default
     *  network, then keeps it fresh via a default-network callback. */
    fun start(context: Context) {
        synchronized(this) {
            if (started) return
            started = true
        }
        val cm = context.applicationContext
            .getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return
        _isOnline.value = hasUsableInternet(cm.getNetworkCapabilities(cm.activeNetwork))
        cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                _isOnline.value = hasUsableInternet(caps)
            }

            override fun onLost(network: Network) {
                _isOnline.value = false
            }

            override fun onUnavailable() {
                _isOnline.value = false
            }
        })
    }

    /** One-shot check for imperative call sites ("can I fire this request
     *  right now?"). Starts the monitor as a side effect. */
    fun isOnlineNow(context: Context): Boolean {
        start(context)
        return _isOnline.value
    }

    /** True only when the network can actually reach the internet — present
     *  AND validated by the system. */
    fun hasUsableInternet(caps: NetworkCapabilities?): Boolean =
        caps != null &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)

    /** Robolectric runs the whole test class in one process, so the singleton
     *  must be resettable between tests. Never called from production code. */
    @VisibleForTesting
    internal fun resetForTest() {
        synchronized(this) { started = false }
        _isOnline.value = true
    }
}
