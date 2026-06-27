package com.plantdiseases.app.push

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.OpinionPrefs
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject
import java.util.Locale

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
object OpinionNotifier : KoinComponent {

    private val scanRepository: ScanRepository by inject()

    /**
     * @param disease the suggested-diagnosis class key (push) or the scan's
     *   own class key (poll).
     * @param note    optional free-text comment the responder left; shown in
     *   the expanded notification when present.
     */
    suspend fun notifyNewOpinion(
        context: Context,
        scanId: String,
        disease: String,
        note: String? = null,
    ) {
        val ctx = context.applicationContext
        if (!OpinionPrefs.notificationsEnabled(ctx)) return
        // On Android 13+ notify() silently no-ops without POST_NOTIFICATIONS —
        // bail before the DB lookup + notification build (matches the reminder
        // worker, which also guards on the runtime permission).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(
                ctx,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        // Localise the disease name to the SAME language the notification text
        // renders in (the app context's effective resource locale), not the
        // in-app language pref — those can disagree (e.g. a Russian device with
        // the pref left at its "en" default), which left the body in Russian
        // but the diagnosis in English.
        val isRu = effectiveLocale(ctx).language == "ru"

        // community scan_id == the local scan's clientScanId — open it directly
        // if we still have it; otherwise drop into the app.
        val localId = scanRepository.getScanByClientScanId(scanId)?.id
        val intent = if (localId != null) {
            Intent(ctx, ResultActivity::class.java)
                .putExtra(ResultActivity.EXTRA_SCAN_ID, localId)
                // Jump straight to the opinions when the notification is tapped.
                .putExtra(ResultActivity.EXTRA_OPEN_OPINIONS, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        } else {
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        val notifId = (scanId.hashCode() and 0x7FFFFFFF)
        val pending = PendingIntent.getActivity(
            ctx,
            notifId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val name = DiseaseLabels.displayName(disease, isRu)
            ?: disease.split('_').joinToString(" ") { it.replaceFirstChar { c -> c.uppercase() } }
        val body = ctx.getString(R.string.opinion_notif_body, name)
        val cleanNote = note?.trim().orEmpty()

        val builder = NotificationCompat.Builder(ctx, PlantDiseasesApp.CHANNEL_COMMUNITY)
            .setSmallIcon(R.drawable.ic_community)
            .setContentTitle(ctx.getString(R.string.opinion_notif_title))
            .setContentText(body)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)

        // When the responder left a comment, surface it in full via the
        // expandable big-text style ("<diagnosis>" then the quoted note).
        if (cleanNote.isNotEmpty()) {
            val expanded = ctx.getString(R.string.opinion_notif_with_note, name, cleanNote)
            builder.setStyle(NotificationCompat.BigTextStyle().bigText(expanded))
        }

        ctx.getSystemService(NotificationManager::class.java).notify(notifId, builder.build())
    }

    private fun effectiveLocale(ctx: Context): Locale {
        val locales = ctx.resources.configuration.locales
        return if (locales.isEmpty) Locale.getDefault() else locales[0]
    }
}
