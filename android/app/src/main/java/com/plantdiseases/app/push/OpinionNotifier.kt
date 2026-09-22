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
import com.plantdiseases.app.di.appEntryPoint
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.NotificationCenter
import com.plantdiseases.app.util.OpinionPrefs

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
 *
 * A plain Kotlin `object` — not an Android framework component — so Hilt
 * can't field-inject it via `@AndroidEntryPoint`; it resolves its repository
 * via [com.plantdiseases.app.di.AppEntryPoint] instead, same as the workers.
 */
object OpinionNotifier {

    /**
     * @param disease the suggested-diagnosis class key (push) or the scan's
     *   own class key (poll).
     * @param note    optional free-text comment the responder left; shown in
     *   the expanded notification when present.
     * @param nickname the responder's plant-vibe community handle; names who
     *   weighed in when present, otherwise the body stays anonymous.
     */
    suspend fun notifyNewOpinion(
        context: Context,
        scanId: String,
        disease: String,
        note: String? = null,
        nickname: String? = null,
    ) {
        val ctx = context.applicationContext
        // Notifications are built off the applicationContext, which — unlike an
        // Activity's context — is never wrapped with the user's chosen app
        // language (LocaleHelper.applyLocale's result is only ever attached via
        // Activity.attachBaseContext). Reading ctx.resources.configuration here
        // would silently fall back to the system locale, disagreeing with a
        // Russian-in-app / English-system user. Resolve the persisted pref
        // directly and wrap a context with it so both the disease name and the
        // string-resource templates below render in the same, correct language.
        val lang = LocaleHelper.getSavedLanguage(ctx)
        val isRu = lang == "ru"
        val localizedCtx = LocaleHelper.setLocale(ctx, lang)

        val name = DiseaseLabels.displayName(disease, isRu)
            ?: disease.split('_').joinToString(" ") { it.replaceFirstChar { c -> c.uppercase() } }
        val cleanNick = nickname?.trim().orEmpty()
        val body = if (cleanNick.isNotEmpty()) {
            localizedCtx.getString(R.string.opinion_notif_body_named, cleanNick, name)
        } else {
            localizedCtx.getString(R.string.opinion_notif_body, name)
        }
        val cleanNote = note?.trim().orEmpty()

        // Recorded before the guards below: whenever this function is reached
        // the row is written, so a user who denied POST_NOTIFICATIONS still
        // finds the opinion in the in-app notification centre instead of
        // losing it entirely. The notificationsEnabled guard below is
        // belt-and-braces — both callers already pre-gate on it (and
        // OpinionCheckWorker gates its network fetch on it too), so a
        // pref-off user does not reach this function at all.
        NotificationCenter.record(
            ctx,
            type = "community",
            title = localizedCtx.getString(R.string.opinion_notif_title),
            body = body,
            deeplink = "community:$scanId",
        )

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

        // community scan_id == the local scan's clientScanId — open the
        // replies screen directly if we still have it; otherwise drop into
        // the app rather than open a screen that would just finish() itself.
        val stillLocal = appEntryPoint(ctx).scanRepository().getScanByClientScanId(scanId) != null
        val intent = if (stillLocal) {
            com.plantdiseases.app.ui.community.MyScanRepliesActivity.intentFor(ctx, scanId)
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

        val builder = NotificationCompat.Builder(localizedCtx, PlantDiseasesApp.CHANNEL_COMMUNITY)
            .setSmallIcon(R.drawable.ic_community)
            .setContentTitle(localizedCtx.getString(R.string.opinion_notif_title))
            .setContentText(body)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)

        // When the responder left a comment, surface it in full via the
        // expandable big-text style ("<diagnosis>" then the quoted note).
        if (cleanNote.isNotEmpty()) {
            val expanded = localizedCtx.getString(R.string.opinion_notif_with_note, name, cleanNote)
            builder.setStyle(NotificationCompat.BigTextStyle().bigText(expanded))
        }

        ctx.getSystemService(NotificationManager::class.java).notify(notifId, builder.build())
    }
}
