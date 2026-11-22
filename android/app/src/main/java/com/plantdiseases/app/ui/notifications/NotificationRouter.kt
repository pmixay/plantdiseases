package com.plantdiseases.app.ui.notifications

import android.content.Context
import android.content.Intent
import com.plantdiseases.app.data.repository.ForumNotificationSync
import com.plantdiseases.app.ui.community.MyScanRepliesActivity
import com.plantdiseases.app.ui.community.forum.ModerationNoticesActivity
import com.plantdiseases.app.ui.community.forum.PostDetailActivity
import com.plantdiseases.app.ui.garden.PlantDetailActivity

/** Resolves a [com.plantdiseases.app.data.local.NotificationEntity.deeplink]
 *  string to the Intent it should open. Plain "prefix:id" strings, no URI
 *  scheme — the whole vocabulary lives here. `null` (no deeplink, or a
 *  prefix from a future app version this build doesn't know) means "open
 *  Home" rather than crash. */
object NotificationRouter {
    fun intentFor(context: Context, deeplink: String?): Intent? {
        if (deeplink.isNullOrBlank()) return null
        val (prefix, id) = deeplink.split(":", limit = 2).let {
            if (it.size == 2) it[0] to it[1] else return null
        }
        return when (prefix) {
            "community" -> MyScanRepliesActivity.intentFor(context, id)
            ForumNotificationSync.LINK_POST -> id.split(":", limit = 2).let { parts ->
                PostDetailActivity.intent(context, parts[0], answerId = parts.getOrNull(1))
            }
            ForumNotificationSync.LINK_DECISION -> ModerationNoticesActivity.intent(context)
            "plant" -> id.toLongOrNull()?.let { plantId ->
                Intent(context, PlantDetailActivity::class.java)
                    .putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plantId)
            }
            else -> null
        }
    }
}
