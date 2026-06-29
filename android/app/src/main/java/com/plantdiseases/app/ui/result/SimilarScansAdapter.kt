package com.plantdiseases.app.ui.result

import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.facebook.shimmer.ShimmerFrameLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.ThumbLoader

/**
 * Horizontal carousel adapter for the "Similar scans near you" card.
 *
 * Each cell renders the publisher's nickname, approximate distance ("≈ N km
 * near you"), and relative time — no place name (geo stays on the server).
 * Cards are ordered newest-first (the server's order). Thumbnails
 * load via Glide from /api/community/thumb/<filename>; cells without a
 * thumb_path fall back to a tinted leaf icon so the card stays usable
 * when the server didn't persist a thumbnail.
 */
class SimilarScansAdapter(
    private val displayName: (String) -> String,
    private val onClick: (CommunityScanItem) -> Unit = {},
    // Preview mode: render each cell as a compact "mini scan result" — diagnosis
    // + detected species — instead of the publisher nickname / distance / opinion
    // affordances. Used by the species card's "others' scans" strip.
    private val preview: Boolean = false,
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
        private val shimmer: ShimmerFrameLayout = itemView.findViewById(R.id.shimmer_similar_thumb)
        private val image: ImageView = itemView.findViewById(R.id.iv_similar_thumb)
        private val label: TextView = itemView.findViewById(R.id.tv_similar_label)
        private val distance: TextView = itemView.findViewById(R.id.tv_similar_distance)
        private val time: TextView = itemView.findViewById(R.id.tv_similar_time)
        private val opinions: TextView = itemView.findViewById(R.id.tv_similar_opinions)
        private val wantsOpinion: TextView = itemView.findViewById(R.id.tv_similar_wants_opinion)

        fun bind(item: CommunityScanItem) {
            itemView.setOnClickListener { onClick(item) }
            if (preview) {
                // Compact mini scan result: diagnosis on top, detected species
                // below. No nickname / distance / time / opinion affordances.
                label.text = displayName(item.disease)
                val species = item.species?.takeIf { it.isNotBlank() }
                distance.text = species.orEmpty()
                distance.visibility = if (species == null) View.GONE else View.VISIBLE
                time.visibility = View.GONE
                opinions.visibility = View.GONE
                wantsOpinion.visibility = View.GONE
            } else {
                // Publisher nick; fall back to the disease label for old rows that
                // predate nicknames so the line is never empty.
                label.text = item.nickname?.takeIf { it.isNotBlank() } ?: displayName(item.disease)
                // Distance only — no place name. Hidden when the feed had no
                // location anchor (country/worldwide scope) so we never show a
                // misleading "0 km" or an empty line.
                val distanceText = formatDistance(item.distanceKm)
                distance.text = distanceText
                distance.visibility = if (distanceText.isEmpty()) View.GONE else View.VISIBLE
                time.text = formatTimeAgo(item.createdAt)
                time.visibility = View.VISIBLE
                wantsOpinion.visibility = if (item.wantsOpinion) View.VISIBLE else View.GONE
                if (item.opinionCount > 0) {
                    opinions.text = itemView.context.getString(
                        R.string.community_opinions_badge,
                        item.opinionCount,
                    )
                    opinions.visibility = View.VISIBLE
                } else {
                    opinions.visibility = View.GONE
                }
            }

            // ServerConfig.baseUrl ends with `/`; relative path appended.
            val thumb = item.thumbPath
            val url = if (thumb.isNullOrBlank()) {
                null
            } else {
                "${ServerConfig.baseUrl}api/community/thumb/$thumb"
            }
            ThumbLoader.load(shimmer, image, url)
        }

        /** "≈ N km near you" — coords are rounded to ~1 km server-side, so we
         *  floor the display at 1 km rather than show a misleading "0 km".
         *  Empty when the feed had no requester anchor (country/worldwide). */
        private fun formatDistance(distanceKm: Float?): String {
            if (distanceKm == null) return ""
            val km = Math.round(distanceKm).coerceAtLeast(1)
            return itemView.context.getString(R.string.community_distance_near, km)
        }

        private fun formatTimeAgo(createdAtSec: Long): String {
            if (createdAtSec <= 0L) return ""
            val createdMs = createdAtSec * 1000L
            val now = System.currentTimeMillis()
            // ``DateUtils`` returns "just now" for deltas under a minute,
            // then "Xm / Xh / Xd ago" — saves us hand-rolling thresholds
            // and translates with the device locale.
            return DateUtils.getRelativeTimeSpanString(
                createdMs,
                now,
                DateUtils.MINUTE_IN_MILLIS,
                DateUtils.FORMAT_ABBREV_RELATIVE,
            ).toString()
        }
    }
}
