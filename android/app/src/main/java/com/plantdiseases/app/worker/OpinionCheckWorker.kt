package com.plantdiseases.app.worker

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
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
        val isRu = LocaleHelper.isRussian(ctx)

        for (scan in resp.scans) {
            if (scan.scanId.isBlank()) continue
            val previous = OpinionPrefs.lastSeen(ctx, scan.scanId)
            if (!firstRun && scan.opinionCount > previous) {
                notifyNewOpinion(ctx, scan.scanId, scan.disease, isRu)
            }
            OpinionPrefs.setSeen(ctx, scan.scanId, scan.opinionCount)
        }
        if (firstRun) OpinionPrefs.markInitialized(ctx)
        return Result.success()
    }

    private suspend fun notifyNewOpinion(
        ctx: Context,
        scanId: String,
        disease: String,
        isRu: Boolean,
    ) {
        val app = ctx as PlantDiseasesApp
        // community scan_id == the local scan's clientScanId — open it directly
        // if we still have it; otherwise drop into the app.
        val localId = app.scanRepository.getScanByClientScanId(scanId)?.id
        val intent = if (localId != null) {
            Intent(ctx, ResultActivity::class.java)
                .putExtra(ResultActivity.EXTRA_SCAN_ID, localId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        } else {
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        val notifId = (scanId.hashCode() and 0x7FFFFFFF)
        val pending = PendingIntent.getActivity(
            ctx, notifId, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val name = DiseaseLabels.displayName(disease, isRu)
            ?: disease.split('_').joinToString(" ") { it.replaceFirstChar { c -> c.uppercase() } }
        val builder = NotificationCompat.Builder(ctx, PlantDiseasesApp.CHANNEL_COMMUNITY)
            .setSmallIcon(R.drawable.ic_community)
            .setContentTitle(ctx.getString(R.string.opinion_notif_title))
            .setContentText(ctx.getString(R.string.opinion_notif_body, name))
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
        ctx.getSystemService(NotificationManager::class.java).notify(notifId, builder.build())
    }
}
