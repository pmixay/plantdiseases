package com.plantdiseases.app.ui.notifications

import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.NotificationEntity

class NotificationsAdapter(
    private val onClick: (NotificationEntity) -> Unit,
) : RecyclerView.Adapter<NotificationsAdapter.VH>() {

    private var items: MutableList<NotificationEntity> = mutableListOf()

    fun submit(newItems: List<NotificationEntity>) {
        items = newItems.toMutableList()
        notifyDataSetChanged()
    }

    fun itemAt(position: Int): NotificationEntity? = items.getOrNull(position)

    fun removeAt(position: Int) {
        if (position < 0 || position >= items.size) return
        items.removeAt(position)
        notifyItemRemoved(position)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_notification, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) = holder.bind(items[position])

    override fun getItemCount(): Int = items.size

    inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val dot: View = itemView.findViewById(R.id.dot_notification_unread)
        private val icon: ImageView = itemView.findViewById(R.id.iv_notification_icon)
        private val title: TextView = itemView.findViewById(R.id.tv_notification_title)
        private val body: TextView = itemView.findViewById(R.id.tv_notification_body)
        private val time: TextView = itemView.findViewById(R.id.tv_notification_time)

        fun bind(item: NotificationEntity) {
            dot.visibility = if (item.readAt == null) View.VISIBLE else View.INVISIBLE
            icon.setImageResource(
                when (item.type) {
                    "community" -> R.drawable.ic_community
                    "reminder" -> R.drawable.ic_watering
                    "garden" -> R.drawable.ic_garden
                    else -> R.drawable.ic_bell
                },
            )
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
}
