package com.plantdiseases.app.ui.garden.detail

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Durable once-per-latest-scan gate for worsening-progression alerts.
 * Plant detail rebuilds several times in a normal visit (initial load, cached
 * care-plan materialisation, onRestart), and each rebuild can receive the same
 * `alert=true` response. Without a persisted identity every response inserted
 * another unread notification-centre row and reposted the system notification.
 */
internal object ProgressionAlertGate {
    internal const val PREFS_NAME = "progression_alert_gate"
    private const val NO_SCAN = Long.MIN_VALUE
    private val mutex = Mutex()

    suspend fun recordOnce(
        context: Context,
        plantId: Long,
        latestScanId: Long,
        record: suspend () -> Unit,
    ): Boolean = mutex.withLock {
        val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val key = "plant_$plantId"
        val alreadyRecorded = withContext(Dispatchers.IO) {
            // Scan ids are AUTOINCREMENT. Suppress an older scan becoming
            // latest again after a newer photo is deleted, too.
            prefs.getLong(key, NO_SCAN) >= latestScanId
        }
        if (alreadyRecorded) return@withLock false

        // Persist only after the notification-centre write succeeds. A
        // transient database failure must remain retryable on the next render.
        record()
        withContext(Dispatchers.IO) {
            prefs.edit().putLong(key, latestScanId).commit()
        }
        true
    }
}
