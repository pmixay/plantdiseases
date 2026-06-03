package com.plantdiseases.app.ui.result

import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.util.ServerConfig

/**
 * Horizontal carousel adapter for the "Similar scans near you" card.
 *
 * Each cell renders disease label, region, and relative time. Thumbnails
 * load via Glide from /api/community/thumb/<filename>; cells without a
 * thumb_path fall back to a tinted leaf icon so the card stays usable
 * when the server didn't persist a thumbnail.
 */
class SimilarScansAdapter(
    private val displayName: (String) -> String,
    private val onClick: (CommunityScanItem) -> Unit = {},
) : RecyclerView.Adapter<SimilarScansAdapter.VH>() {

    private var items: List<CommunityScanItem> = emptyList()

    fun submit(newItems: List<CommunityScanItem>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_similar_scan, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val image: ImageView = itemView.findViewById(R.id.iv_similar_thumb)
        private val label: TextView = itemView.findViewById(R.id.tv_similar_label)
        private val region: TextView = itemView.findViewById(R.id.tv_similar_region)
        private val time: TextView = itemView.findViewById(R.id.tv_similar_time)

        fun bind(item: CommunityScanItem) {
            itemView.setOnClickListener { onClick(item) }
            label.text = displayName(item.disease)
            region.text = formatRegion(item.region, item.country)
            time.text = formatTimeAgo(item.createdAt)

            val thumb = item.thumbPath
            if (thumb.isNullOrBlank()) {
                Glide.with(itemView).clear(image)
                image.setImageResource(R.drawable.ic_disease)
            } else {
                // ServerConfig.baseUrl ends with `/`; relative path appended.
                val url = "${ServerConfig.baseUrl}api/community/thumb/$thumb"
                Glide.with(itemView)
                    .load(url)
                    .placeholder(R.drawable.ic_disease)
                    .error(R.drawable.ic_disease)
                    .centerCrop()
                    .into(image)
            }
        }

        private fun formatRegion(region: String, country: String): String {
            val r = region.trim()
            val c = country.trim()
            return when {
                r.isNotEmpty() && c.isNotEmpty() -> "$r, $c"
                r.isNotEmpty() -> r
                c.isNotEmpty() -> c
                else -> ""
            }
        }

        private fun formatTimeAgo(createdAtSec: Long): String {
            if (createdAtSec <= 0L) return ""
            val createdMs = createdAtSec * 1000L
            val now = System.currentTimeMillis()
            // ``DateUtils`` returns "just now" for deltas under a minute,
            // then "Xm / Xh / Xd ago" — saves us hand-rolling thresholds
            // and translates with the device locale.
            return DateUtils.getRelativeTimeSpanString(
                createdMs, now, DateUtils.MINUTE_IN_MILLIS,
                DateUtils.FORMAT_ABBREV_RELATIVE,
            ).toString()
        }
    }
}
