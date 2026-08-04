package com.plantdiseases.app.util

import android.view.View
import android.widget.Button
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.launch

// How screens react to the shared ConnectivityObserver, without each one
// hand-rolling a `lifecycleScope.launch { repeatOnLifecycle { collect } }`.
//
// Collection is always scoped to the owner's STARTED window, so an offscreen
// screen holds no network callback and no probe. That scoping is the whole
// reason the observer costs nothing in the background, and it is too easy to
// get subtly wrong one call site at a time.
//
// Fragments must pass `viewLifecycleOwner`, not `this`.

/** Run [block] with the current reachability state while [this] is STARTED. */
fun LifecycleOwner.onConnectivityChanged(
    connectivity: ConnectivityObserver,
    block: (online: Boolean) -> Unit,
) {
    lifecycleScope.launch {
        repeatOnLifecycle(Lifecycle.State.STARTED) {
            connectivity.isOnline.collect(block)
        }
    }
}

/**
 * Enable [views] only while the server is reachable — the standard treatment
 * for an entry point into an online-only feature (community, species ID,
 * symptom triage, the AI doctor).
 *
 * Disabled rather than hidden: a control that vanishes reads as a bug or a
 * lost feature, while a greyed-out one reads as "not right now" and keeps the
 * screen's layout stable when connectivity flaps. A disabled view still
 * swallows its touches without firing its click listener, so this is enough
 * to close the entry point.
 */
fun LifecycleOwner.enableWhileOnline(
    connectivity: ConnectivityObserver,
    vararg views: View,
) {
    enableWhileOnline(connectivity.isOnline, *views)
}

/** Flow-based seam, so the gate can be tested without a ConnectivityManager. */
internal fun LifecycleOwner.enableWhileOnline(
    onlineStates: Flow<Boolean>,
    vararg views: View,
) {
    lifecycleScope.launch {
        repeatOnLifecycle(Lifecycle.State.STARTED) {
            onlineStates.collect { online ->
                views.forEach { view ->
                    view.isEnabled = online
                    // Buttons already dim their own disabled state — stacking a
                    // view alpha on top of Material's leaves them almost
                    // invisible. Cards and plain layouts don't, so those get it.
                    if (view !is Button) view.alpha = if (online) 1f else DISABLED_ALPHA
                }
            }
        }
    }
}

/** Enough dimming to read as unavailable without hiding the control. */
private const val DISABLED_ALPHA = 0.45f
