package com.plantdiseases.app.worker

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.push.OpinionNotifier
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.TelemetryIds

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
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val app = ctx as PlantDiseasesApp
        val pseudoId = TelemetryIds.userPseudoId(ctx)

        val resp = app.scanRepository.fetchMyScanOpinions(pseudoId).getOrElse {
            // Network/server hiccup — retry on the next interval.
            return Result.retry()
        }

        val firstRun = !OpinionPrefs.isInitialized(ctx)

        for (scan in resp.scans) {
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
