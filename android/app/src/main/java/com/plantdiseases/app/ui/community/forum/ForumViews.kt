package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.res.ColorStateList
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.core.content.ContextCompat
import com.bumptech.glide.Glide
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumAuthor
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.util.Avatars
import kotlin.math.roundToInt

/** Builds a profile photo URL from an author token and its `avatar_version`. */
typealias AvatarUrl = (authorToken: String, version: Long) -> String

/** View binding shared by the feed card and the thread: avatar, status pill, meta line. */
object ForumViews {

    /** A post's state, shown as a coloured pill. */
    enum class Status(
        @StringRes val text: Int,
        @ColorRes val foreground: Int,
        @ColorRes val background: Int,
        @DrawableRes val icon: Int?,
    ) {
        SOLVED(R.string.forum_badge_solved, R.color.healthy_green, R.color.healthy_green_bg, R.drawable.ic_check),
        URGENT(R.string.forum_badge_urgent, R.color.disease_red, R.color.disease_red_bg, R.drawable.ic_clock),
        UNANSWERED(R.string.forum_badge_unanswered, R.color.warning_amber, R.color.warning_amber_bg, null),
        PINNED(R.string.forum_badge_pinned, R.color.primary, R.color.primary_light, R.drawable.ic_pin),
        LOCKED(R.string.forum_badge_locked, R.color.on_surface, R.color.surface_container, R.drawable.ic_lock),
        HIDDEN(R.string.forum_badge_hidden, R.color.warning_amber, R.color.warning_amber_bg, null),
    }

    /** Feed order of importance: solved, urgent, waiting for a first answer, pinned. */
    fun feedStatus(post: ForumPost): Status? = when {
        post.solved -> Status.SOLVED
        post.urgent -> Status.URGENT
        post.answerCount == 0 && (post.kind == "question" || post.kind == "scan") -> Status.UNANSWERED
        post.pinned -> Status.PINNED
        else -> null
    }

    /** Thread header: moderation states matter more than "unanswered" there. */
    fun threadStatus(post: ForumPost): Status? = when {
        post.solved -> Status.SOLVED
        post.locked -> Status.LOCKED
        post.state == "hidden" -> Status.HIDDEN
        post.urgent -> Status.URGENT
        else -> null
    }

    fun bindStatus(view: TextView, status: Status?) {
        if (status == null) {
            view.visibility = View.GONE
            return
        }
        val context = view.context
        val foreground = ContextCompat.getColor(context, status.foreground)
        view.visibility = View.VISIBLE
        view.setText(status.text)
        view.setTextColor(foreground)
        view.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(context, status.background))
        setSmallIcon(view, status.icon, PILL_ICON_DP)
        view.compoundDrawableTintList = ColorStateList.valueOf(foreground)
    }

    /**
     * The one place a forum avatar is bound. The monogram (a colour derived
     * from the nickname) is always set; when the author shows a profile photo
     * it is loaded over it, circle-cropped. Until the photo arrives, or if it
     * fails to load, the transparent photo view leaves the monogram showing.
     */
    fun bindAvatar(
        monogram: TextView,
        photo: ImageView,
        nickname: String,
        author: ForumAuthor?,
        avatarUrl: AvatarUrl,
    ) {
        monogram.text = Avatars.monogramForNick(nickname)
        monogram.backgroundTintList = ColorStateList.valueOf(Avatars.colorFor(nickname))
        val token = author?.authorToken
        val version = author?.avatarVersion
        if (token.isNullOrBlank() || version == null) {
            Glide.with(photo).clear(photo)
            photo.visibility = View.GONE
            return
        }
        photo.visibility = View.VISIBLE
        Glide.with(photo).load(avatarUrl(token, version)).circleCrop().into(photo)
    }

    /** "Tip, Pests, 5 days ago"; questions are the default kind and are not named. */
    fun meta(context: Context, post: ForumPost, spaces: List<ForumSpace>): String {
        val parts = buildList {
            if (post.kind != "question") add(context.getString(ForumUi.kindLabel(post.kind)))
            add(ForumUi.spaceName(context, post.space, spaces))
            add(ForumUi.relativeTime(post.createdAt).toString())
        }
        return parts.joinToString(", ")
    }

    /** Puts [icon] before the text at [sizeDp], or removes it when null. */
    fun setSmallIcon(view: TextView, @DrawableRes icon: Int?, sizeDp: Int) {
        val drawable = icon?.let { ContextCompat.getDrawable(view.context, it)?.mutate() }
        if (drawable != null) {
            val size = (sizeDp * view.resources.displayMetrics.density).roundToInt()
            drawable.setBounds(0, 0, size, size)
        }
        view.setCompoundDrawablesRelative(drawable, null, null, null)
    }

    private const val PILL_ICON_DP = 14
}
