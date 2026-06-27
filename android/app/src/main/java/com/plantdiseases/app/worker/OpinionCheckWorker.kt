package com.plantdiseases.app.worker

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.push.OpinionNotifier
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.TelemetryIds
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Periodically polls the server for second opinions on THIS install's shared
 * scans and posts a notification when a new one appears. The per-scan view
 * already exists (the result screen's "View opinions" button) — this just
 * tells the user there's something new to look at.
 *
 * Quiet by design: the first successful poll seeds the baseline without
 * notifying (so pre-existing opinions don't all fire at once), and each scan
 * only notifies when its opinion count actually grows.
 */
class OpinionCheckWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params), KoinComponent {

    private val scanRepository: ScanRepository by inject()

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        if (!OpinionPrefs.notificationsEnabled(ctx)) return Result.success()
        val pseudoId = TelemetryIds.userPseudoId(ctx)

        val resp = scanRepository.fetchMyScanOpinions(pseudoId).getOrElse {
            // Network/server hiccup — retry on the next interval.
            return Result.retry()
        }

        val firstRun = !OpinionPrefs.isInitialized(ctx)

        // .orEmpty() guards the case where Gson injects an explicit JSON
        // `"scans": null` into the non-null field (it ignores the Kotlin
        // default for present-but-null keys) — iterating null would NPE.
        for (scan in resp.scans.orEmpty()) {
            if (scan.scanId.isBlank()) continue
            val previous = OpinionPrefs.lastSeen(ctx, scan.scanId)
            if (!firstRun && scan.opinionCount > previous) {
                OpinionNotifier.notifyNewOpinion(ctx, scan.scanId, scan.disease)
            }
            OpinionPrefs.setSeen(ctx, scan.scanId, scan.opinionCount)
        }
        if (firstRun) OpinionPrefs.markInitialized(ctx)
        return Result.success()
    }
}
