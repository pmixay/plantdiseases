package com.plantdiseases.app.push

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper

/**
 * Builds + posts the "someone left a second opinion on your scan" notification.
 * Shared by both delivery paths so they look and behave identically:
 *   • [com.plantdiseases.app.push.PlantMessagingService] — FCM push (works
 *     with the app closed / phone idle), the primary path.
 *   • [com.plantdiseases.app.worker.OpinionCheckWorker] — the WorkManager poll
 *     that still runs as a fallback (and on app foreground).
 *
 * The notification deep-links to the scan's result screen when the scan is
 * still in local history; otherwise it opens the app.
 */
object OpinionNotifier {

    suspend fun notifyNewOpinion(context: Context, scanId: String, disease: String) {
        val ctx = context.applicationContext
        val app = ctx as PlantDiseasesApp
        val isRu = LocaleHelper.isRussian(ctx)

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
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
        ctx.getSystemService(NotificationManager::class.java).notify(notifId, builder.build())
    }
}
