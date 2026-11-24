package com.plantdiseases.app.ui.community

import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.facebook.shimmer.ShimmerFrameLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ThumbLoader

/**
 * The list the community map opens for a cluster of scans. Each row renders
 * the diagnosis with the place and distance/time, a views count and the scan
 * photo full-width. No author: scans are shown without identity. A tap on
 * the photo hands [onOpen] the scan and the photo view, which the caller
 * expands full screen.
 *
 * Thumbnails load via Glide from /api/community/thumb/<filename>; scans
 * without a thumb_path fall back to a tinted leaf icon so the row stays
 * usable when the server didn't persist a thumbnail.
 */
class NearbyScansAdapter(
    private val displayName: (String) -> String,
    private val onOpen: (CommunityScanItem, View) -> Unit,
) : RecyclerView.Adapter<NearbyScansAdapter.VH>() {

    private var items: List<CommunityScanItem> = emptyList()

    fun submit(newItems: List<CommunityScanItem>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_nearby_scan, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) = holder.bind(items[position])

    /** Release the pooled holder's bitmap instead of pinning a 260dp photo
     *  in every off-screen holder. */
    override fun onViewRecycled(holder: VH) {
        super.onViewRecycled(holder)
        holder.clearPhoto()
    }

    override fun getItemCount(): Int = items.size

    inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val shimmer: ShimmerFrameLayout = itemView.findViewById(R.id.shimmer_nearby_photo)
        private val photo: ImageView = itemView.findViewById(R.id.iv_nearby_photo)
        private val label: TextView = itemView.findViewById(R.id.tv_nearby_label)
        private val meta: TextView = itemView.findViewById(R.id.tv_nearby_meta)
        private val whenLine: TextView = itemView.findViewById(R.id.tv_nearby_when)
        private val views: TextView = itemView.findViewById(R.id.tv_nearby_views)

        fun bind(item: CommunityScanItem) {
            label.text = displayName(item.disease)
            meta.text = buildMeta(item)
            // Distance + time live on their own line so a long place name can't
            // ellipsize them away.
            val whenText = buildWhen(item)
            whenLine.text = whenText
            whenLine.visibility = if (whenText.isEmpty()) View.GONE else View.VISIBLE
            // Always show the count, zero included, so every row looks the same.
            views.text = item.viewCount.toString()
            photo.setOnClickListener { onOpen(item, photo) }
            ThumbLoader.load(shimmer, photo, item.photoUrl())
        }

        fun clearPhoto() {
            Glide.with(photo).clear(photo)
        }

        /** "Warsaw, Poland" — just the place. Distance + time live in
         *  [buildWhen] so a long place name can't push them off the end of
         *  this single (ellipsized) line. Missing pieces drop out. */
        private fun buildMeta(item: CommunityScanItem): String {
            val locality = item.city.ifBlank { item.region }
            return formatRegion(locality, countryLabel(item))
        }

        /** "≈ 3 km, 2 hr. ago" — the distance (nearby scope only) + the
         *  relative time, on their own always-visible line. */
        private fun buildWhen(item: CommunityScanItem): String =
            listOf(
                formatDistance(item.distanceKm),
                formatTimeAgo(item.createdAt),
            ).filter { it.isNotEmpty() }.joinToString(", ")

        /** "≈ N km" — the whole screen is "near you", so the suffix is
         *  redundant and only stole space from the recency stamp. Coords are
         *  rounded to ~1 km server-side, so we floor the display at 1 km
         *  rather than show a misleading "0 km". Empty when the feed had no
         *  requester anchor (country/worldwide). */
        private fun formatDistance(distanceKm: Float?): String {
            if (distanceKm == null) return ""
            val km = Math.round(distanceKm).coerceAtLeast(1)
            return itemView.context.getString(R.string.community_distance_near, km)
        }

        /** Localized country NAME (e.g. "Poland"/"Польша") the server resolved,
         *  falling back to the raw [CommunityScanItem.country] code only for
         *  older/cached responses that predate the localized fields. */
        private fun countryLabel(item: CommunityScanItem): String {
            val name = if (LocaleHelper.isRussian(itemView.context)) {
                item.countryNameRu
            } else {
                item.countryNameEn
            }
            return (name ?: item.country)
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
            return DateUtils.getRelativeTimeSpanString(
                createdMs,
                now,
                DateUtils.MINUTE_IN_MILLIS,
                DateUtils.FORMAT_ABBREV_RELATIVE,
            ).toString()
        }
    }
}
