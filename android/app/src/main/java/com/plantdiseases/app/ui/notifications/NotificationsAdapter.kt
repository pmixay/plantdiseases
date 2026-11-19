package com.plantdiseases.app.ui.notifications

import android.content.res.ColorStateList
import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.NotificationEntity
import com.plantdiseases.app.data.repository.ForumNotificationSync

/**
 * The notification centre's list: unread rows under "New", the rest under
 * "Earlier". Headers are rows of their own; [itemAt] returns null for them,
 * which also keeps them from being swiped away.
 */
class NotificationsAdapter(
    private val onClick: (NotificationEntity) -> Unit,
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private sealed interface Row {
        data class Header(@StringRes val text: Int) : Row
        data class Item(val entity: NotificationEntity) : Row
    }

    private var rows: MutableList<Row> = mutableListOf()

    fun submit(items: List<NotificationEntity>) {
        val (unread, read) = items.partition { it.readAt == null }
        rows = buildList {
            if (unread.isNotEmpty()) {
                add(Row.Header(R.string.notifications_section_new))
                unread.forEach { add(Row.Item(it)) }
            }
            if (read.isNotEmpty()) {
                add(Row.Header(R.string.notifications_section_earlier))
                read.forEach { add(Row.Item(it)) }
            }
        }.toMutableList()
        notifyDataSetChanged()
    }

    fun itemAt(position: Int): NotificationEntity? = (rows.getOrNull(position) as? Row.Item)?.entity

    /** Removes a row and, if it was the last under its heading, the heading. */
    fun removeAt(position: Int) {
        if (itemAt(position) == null) return
        rows.removeAt(position)
        notifyItemRemoved(position)
        val above = position - 1
        val headingAlone = rows.getOrNull(above) is Row.Header && rows.getOrNull(position) !is Row.Item
        if (headingAlone) {
            rows.removeAt(above)
            notifyItemRemoved(above)
        }
    }

    /** Notification rows only; headings don't count. */
    fun notificationCount(): Int = rows.count { it is Row.Item }

    override fun getItemViewType(position: Int): Int = if (rows[position] is Row.Header) TYPE_HEADER else TYPE_ITEM

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == TYPE_HEADER) {
            HeaderVH(inflater.inflate(R.layout.item_notification_header, parent, false) as TextView)
        } else {
            VH(inflater.inflate(R.layout.item_notification, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val row = rows[position]) {
            is Row.Header -> (holder as HeaderVH).text.setText(row.text)
            is Row.Item -> (holder as VH).bind(row.entity)
        }
    }

    override fun getItemCount(): Int = rows.size

    private class HeaderVH(val text: TextView) : RecyclerView.ViewHolder(text)

    private inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val dot: View = itemView.findViewById(R.id.dot_notification_unread)
        private val icon: ImageView = itemView.findViewById(R.id.iv_notification_icon)
        private val title: TextView = itemView.findViewById(R.id.tv_notification_title)
        private val body: TextView = itemView.findViewById(R.id.tv_notification_body)
        private val time: TextView = itemView.findViewById(R.id.tv_notification_time)

        fun bind(item: NotificationEntity) {
            val context = itemView.context
            dot.visibility = if (item.readAt == null) View.VISIBLE else View.GONE
            val style = Style.of(item.type)
            icon.setImageResource(style.icon)
            icon.imageTintList = ColorStateList.valueOf(ContextCompat.getColor(context, style.foreground))
            icon.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(context, style.background))
            title.text = item.title
            body.text = item.body
            time.text = DateUtils.getRelativeTimeSpanString(
                item.createdAt,
                System.currentTimeMillis(),
                DateUtils.MINUTE_IN_MILLIS,
                DateUtils.FORMAT_ABBREV_RELATIVE,
            )
            itemView.setOnClickListener { onClick(item) }
        }
    }

    /** Icon and colours per notification type. */
    private enum class Style(
        @DrawableRes val icon: Int,
        @ColorRes val foreground: Int,
        @ColorRes val background: Int,
    ) {
        ANSWER(R.drawable.ic_comment, R.color.warning_amber, R.color.warning_amber_bg),
        ACCEPTED(R.drawable.ic_check, R.color.primary, R.color.primary_light),
        DECISION(R.drawable.ic_flag, R.color.disease_red, R.color.disease_red_bg),
        SCAN_REPLY(R.drawable.ic_community, R.color.primary, R.color.primary_light),
        REMINDER(R.drawable.ic_watering, R.color.neutral_blue, R.color.neutral_blue_bg),
        GARDEN(R.drawable.ic_garden, R.color.primary, R.color.primary_light),
        OTHER(R.drawable.ic_bell, R.color.on_surface_secondary, R.color.surface_container),
        ;

        companion object {
            fun of(type: String): Style = when (type) {
                ForumNotificationSync.TYPE_ANSWER -> ANSWER
                ForumNotificationSync.TYPE_ACCEPTED -> ACCEPTED
                ForumNotificationSync.TYPE_DECISION -> DECISION
                "community" -> SCAN_REPLY
                "reminder" -> REMINDER
                "garden" -> GARDEN
                else -> OTHER
            }
        }
    }

    private companion object {
        const val TYPE_HEADER = 0
        const val TYPE_ITEM = 1
    }
}
