package com.plantdiseases.app.data.repository

import android.content.Context
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.NotificationEntity
import com.plantdiseases.app.data.model.ForumActivityItem
import com.plantdiseases.app.data.model.ModNotice
import com.plantdiseases.app.ui.community.forum.ForumUi
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Brings Community events into the notification centre: answers on the
 * user's posts, replies to their answers, accepted answers, and moderators'
 * decisions. Each event is stored once, as an ordinary notification row, so
 * the bell's count, read state, swipe and "clear all" treat it like any
 * other. A watermark (the newest event already stored) keeps rows the user
 * dismissed from coming back.
 */
@Singleton
class ForumNotificationSync @Inject constructor(
    @ApplicationContext private val context: Context,
    private val forum: ForumRepository,
    private val notifications: NotificationRepository,
) {
    private val lock = Mutex()

    /** Decisions as last fetched, for the one-time popup in Community. */
    @Volatile
    var notices: List<ModNotice> = emptyList()
        private set

    /** What one [sync] stored: the new events, and whether this was the
     *  first import (history, which must not pop up as notifications). */
    class Imported(val events: List<Event>, val firstImport: Boolean)

    /** Fetches and stores new events. A no-op when signed out or offline. */
    suspend fun sync(): Imported = lock.withLock {
        val none = Imported(emptyList(), firstImport = false)
        if (!forum.isSignedIn) return@withLock none
        val activity = forum.activity(since = 0).getOrNull()?.items
        val decisions = forum.notices().getOrNull()?.items
        decisions?.let { notices = it }
        if (activity == null && decisions == null) return@withLock none
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val watermark = prefs.getLong(KEY_WATERMARK, 0L)
        val events = Plan.events(activity.orEmpty(), decisions.orEmpty(), watermark)
        if (events.isEmpty()) return@withLock none
        val now = System.currentTimeMillis() / MILLIS
        events.forEach { event ->
            // On the first sync, history older than a week arrives already read.
            val read = watermark == 0L && now - event.createdAt > FIRST_SYNC_UNREAD_S
            notifications.insert(
                NotificationEntity(
                    type = event.type,
                    title = context.getString(event.title),
                    body = event.body(context),
                    deeplink = event.deeplink,
                    createdAt = event.createdAt * MILLIS,
                    readAt = if (read) event.createdAt * MILLIS else null,
                ),
            )
        }
        prefs.edit().putLong(KEY_WATERMARK, events.maxOf { it.createdAt }).apply()
        Imported(events, firstImport = watermark == 0L)
    }

    /** One Community event, ready to store. Times are in seconds. [postId],
     *  [answerId] and [postTitle] are set for answer events. */
    class Event(
        val type: String,
        val title: Int,
        val deeplink: String,
        val createdAt: Long,
        val postId: String? = null,
        val answerId: String? = null,
        val postTitle: String? = null,
        val body: (Context) -> String,
    )

    /** What to store, kept free of Android so it can be tested directly. */
    object Plan {
        fun events(activity: List<ForumActivityItem>, decisions: List<ModNotice>, watermark: Long): List<Event> {
            val answers = activity.filter { it.createdAt > watermark }.map { item ->
                val title = when (item.type) {
                    "answer_accepted" -> R.string.forum_notif_accepted_title
                    "reply_to_answer" -> R.string.forum_notif_reply_title
                    else -> R.string.forum_notif_answer_title
                }
                Event(
                    type = if (item.type == "answer_accepted") TYPE_ACCEPTED else TYPE_ANSWER,
                    title = title,
                    deeplink = postLink(item.postId, item.answerId),
                    createdAt = item.createdAt,
                    postId = item.postId,
                    answerId = item.answerId,
                    postTitle = item.postTitle,
                ) { context -> activityText(context, item) }
            }
            val moderation = decisions.filter { it.createdAt > watermark }.map { notice ->
                Event(
                    type = TYPE_DECISION,
                    title = R.string.forum_decision_title,
                    deeplink = "$LINK_DECISION:${notice.actionId}",
                    createdAt = notice.createdAt,
                ) { context -> ForumUi.modNoticeHeading(context, notice) }
            }
            return (answers + moderation).sortedBy { it.createdAt }
        }

        private fun activityText(context: Context, item: ForumActivityItem): String {
            val post = item.postTitle?.takeIf { it.isNotBlank() } ?: context.getString(R.string.forum_kind_scan)
            return when (item.type) {
                "answer_accepted" -> context.getString(R.string.forum_activity_accepted, post)
                "reply_to_answer" -> context.getString(R.string.forum_activity_reply, item.nickname, post)
                else -> context.getString(R.string.forum_activity_answer, item.nickname, post)
            }
        }
    }

    companion object {
        const val TYPE_ANSWER = "forum_answer"
        const val TYPE_ACCEPTED = "forum_accepted"
        const val TYPE_DECISION = "forum_decision"
        const val LINK_POST = "forum_post"
        const val LINK_DECISION = "forum_decision"
        private const val PREFS = "forum_notifications"

        /** "forum_post:<post>" or "forum_post:<post>:<answer>", which opens the
         *  post scrolled to that answer (NotificationRouter). */
        fun postLink(postId: String, answerId: String?): String =
            if (answerId.isNullOrBlank()) "$LINK_POST:$postId" else "$LINK_POST:$postId:$answerId"
        private const val KEY_WATERMARK = "imported_until"
        private const val MILLIS = 1000L
        private const val FIRST_SYNC_UNREAD_S = 7 * 24 * 60 * 60L
    }
}
