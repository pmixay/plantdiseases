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
// These take the flow rather than the observer so the same code path is what
// the tests exercise. Fragments must pass `viewLifecycleOwner`, not `this`.

/** Run [block] with the current reachability state while [this] is STARTED. */
fun LifecycleOwner.onConnectivityChanged(
    onlineStates: Flow<Boolean>,
    block: (online: Boolean) -> Unit,
) {
    lifecycleScope.launch {
        repeatOnLifecycle(Lifecycle.State.STARTED) {
            onlineStates.collect(block)
        }
    }
}

/**
 * Run [block] each time the server becomes reachable *again* — for a screen
 * whose content is live-only and was deliberately not fetched while offline.
 *
 * The first reading never fires it; only a false→true transition does, so a
 * screen that opens online doesn't load twice.
 */
fun LifecycleOwner.onReconnected(onlineStates: Flow<Boolean>, block: () -> Unit) {
    var wasOnline = true
    onConnectivityChanged(onlineStates) { online ->
        if (online && !wasOnline) block()
        wasOnline = online
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
 *
 * A view whose enabled state something *else* also writes needs that writer to
 * consult the same signal — see `CommunityController`, where a re-render used
 * to quietly undo this.
 */
fun LifecycleOwner.enableWhileOnline(
    onlineStates: Flow<Boolean>,
    vararg views: View,
) = onConnectivityChanged(onlineStates) { online ->
    views.forEach { view ->
        view.isEnabled = online
        // Buttons already dim their own disabled state — stacking a view alpha
        // on top of Material's leaves them almost invisible. Cards and plain
        // layouts don't, so those get the alpha.
        if (view !is Button) view.alpha = if (online) 1f else DISABLED_ALPHA
    }
}

/** Enough dimming to read as unavailable without hiding the control. */
private const val DISABLED_ALPHA = 0.45f
