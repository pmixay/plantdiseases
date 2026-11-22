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
import com.plantdiseases.app.data.repository.ForumNotificationSync
import com.plantdiseases.app.ui.community.forum.PostDetailActivity
import com.plantdiseases.app.util.ForumNotificationPrefs
import com.plantdiseases.app.util.LocaleHelper

/**
 * System notifications for answers on the user's posts, replies to their
 * answers and accepted answers. Tapping one opens the post scrolled to that
 * answer.
 *
 * Without FCM in production these come from [com.plantdiseases.app.worker.
 * ForumAnswerCheckWorker]'s poll, and never repeat what the in-app notification
 * centre already imported. Spam limits ([Plan]): answers on one post share one
 * notification ("3 new answers"), at most [MAX_ALERTS_PER_HOUR] notifications
 * alert per hour (the rest fold into one silent summary), history from the
 * first import and anything older than a day never pops up, and the user can
 * turn them off in Profile ([ForumNotificationPrefs]).
 */
object ForumAnswerNotifier {
    const val MAX_ALERTS_PER_HOUR = 3
    const val FRESH_FOR_S = 24 * 60 * 60L
    private const val HOUR_S = 60 * 60L
    private const val SUMMARY_ID = 0x0F0A0001

    /** One notification to post: the newest event on [postId] and how many
     *  new events that post has. */
    data class Push(val postId: String, val count: Int, val newest: ForumNotificationSync.Event)

    /** [alerts] notify now; [folded] more posts wait in the silent summary. */
    data class Decision(val alerts: List<Push>, val folded: Int)

    /** What to post, kept free of Android so it can be tested directly. */
    object Plan {
        fun decide(
            imported: ForumNotificationSync.Imported,
            nowS: Long,
            recentAlertsS: List<Long>,
            enabled: Boolean,
        ): Decision {
            if (!enabled || imported.firstImport) return Decision(emptyList(), 0)
            val perPost = imported.events
                .filter { it.postId != null && nowS - it.createdAt <= FRESH_FOR_S }
                .filter {
                    it.type == ForumNotificationSync.TYPE_ANSWER || it.type == ForumNotificationSync.TYPE_ACCEPTED
                }
                .groupBy { it.postId!! }
                .map { (postId, events) -> Push(postId, events.size, events.maxBy { it.createdAt }) }
                .sortedByDescending { it.newest.createdAt }
            val budget = (MAX_ALERTS_PER_HOUR - recentAlertsS.count { nowS - it < HOUR_S }).coerceAtLeast(0)
            return Decision(perPost.take(budget), (perPost.size - budget).coerceAtLeast(0))
        }
    }

    fun notify(context: Context, imported: ForumNotificationSync.Imported) {
        val ctx = context.applicationContext
        val nowS = System.currentTimeMillis() / MILLIS
        val decision = Plan.decide(
            imported,
            nowS,
            ForumNotificationPrefs.recentAlerts(ctx),
            ForumNotificationPrefs.answersEnabled(ctx),
        )
        if (decision.alerts.isEmpty() && decision.folded == 0) return
        if (!canPost(ctx)) return
        // Notifications are built off the application context, which never
        // carries the in-app language: wrap it with the saved one.
        val localized = LocaleHelper.setLocale(ctx, LocaleHelper.getSavedLanguage(ctx))
        val manager = ctx.getSystemService(NotificationManager::class.java)
        decision.alerts.forEach { push ->
            manager.notify(push.postId.hashCode() and 0x7FFFFFFF, build(localized, push))
        }
        if (decision.alerts.isNotEmpty()) ForumNotificationPrefs.recordAlerts(ctx, nowS, decision.alerts.size)
        if (decision.folded > 0) manager.notify(SUMMARY_ID, summary(localized, decision.folded))
    }

    private fun canPost(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    private fun build(ctx: Context, push: Push): android.app.Notification {
        val event = push.newest
        val title = ctx.getString(event.title)
        val body = if (push.count > 1) {
            val post = event.postTitle?.takeIf { it.isNotBlank() } ?: ctx.getString(R.string.forum_kind_scan)
            ctx.resources.getQuantityString(R.plurals.forum_notif_answers_count, push.count, push.count, post)
        } else {
            event.body(ctx)
        }
        val intent = PostDetailActivity.intent(ctx, push.postId, answerId = event.answerId)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(
            ctx,
            push.postId.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(ctx, PlantDiseasesApp.CHANNEL_FORUM_ANSWERS)
            .setSmallIcon(R.drawable.ic_community)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
    }

    private fun summary(ctx: Context, posts: Int): android.app.Notification {
        val intent = Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pending = PendingIntent.getActivity(
            ctx,
            SUMMARY_ID,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(ctx, PlantDiseasesApp.CHANNEL_FORUM_ANSWERS)
            .setSmallIcon(R.drawable.ic_community)
            .setContentTitle(ctx.getString(R.string.forum_notif_more_title))
            .setContentText(ctx.resources.getQuantityString(R.plurals.forum_notif_more_body, posts, posts))
            .setContentIntent(pending)
            .setAutoCancel(true)
            // Over the hourly limit: update quietly instead of alerting again.
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private const val MILLIS = 1000L
}
