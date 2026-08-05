package com.plantdiseases.app.util

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow

/**
 * Where every real API call reports whether the server answered it.
 *
 * This is the app's primary reachability signal and it is free: the requests
 * are ones the app was making anyway. [ConnectivityObserver] consumes the
 * stream instead of polling a health endpoint on a timer, so a device that is
 * online costs exactly zero extra network traffic.
 *
 * The contract is deliberately narrow — "did the server answer", not "did the
 * call succeed":
 *  - any HTTP response, including 4xx/5xx, is `true`. The bytes made it there
 *    and back; a failing endpoint is not an offline device.
 *  - only a transport-level [java.io.IOException] (DNS, connect, TLS, timeout),
 *    after the retry interceptor has given up, is `false`.
 *
 * A `false` report is a hint, not a verdict: a single request can die on a
 * network handover while the server is perfectly reachable, so the observer
 * confirms with a probe before it flips the UI.
 *
 * Events only — no replay. A stale outcome from before the app went into the
 * background must never be re-delivered as if it were current.
 */
class ReachabilityReporter {

    private val events = MutableSharedFlow<Boolean>(
        replay = 0,
        extraBufferCapacity = 8,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    /** Outcomes of real API calls, newest first-come. Never completes. */
    val reports: Flow<Boolean> = events

    /** Record one call's outcome. Non-blocking and safe from any thread. */
    fun report(reachable: Boolean) {
        events.tryEmit(reachable)
    }
}
