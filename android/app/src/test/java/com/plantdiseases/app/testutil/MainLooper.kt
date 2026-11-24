package com.plantdiseases.app.testutil

import android.os.Looper
import org.junit.Assert.fail
import org.robolectric.Shadows.shadowOf

/**
 * Runs the main looper until [condition] holds. For screens that read Room on a
 * background dispatcher and render on main: a write made by the test reaches the
 * view only after a thread hop each way, so a single `idle()` is not enough.
 */
fun awaitOnMainLooper(timeoutMs: Long = 5_000L, condition: () -> Boolean) {
    val deadline = System.currentTimeMillis() + timeoutMs
    while (true) {
        shadowOf(Looper.getMainLooper()).idle()
        if (condition()) return
        if (System.currentTimeMillis() > deadline) fail("condition not met within $timeoutMs ms")
        Thread.sleep(POLL_MS)
    }
}

private const val POLL_MS = 20L
