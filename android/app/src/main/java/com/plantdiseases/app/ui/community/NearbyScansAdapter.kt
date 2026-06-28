package com.plantdiseases.app.ui.community

import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.facebook.shimmer.ShimmerFrameLayout
import com.google.android.material.button.MaterialButton
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.ThumbLoader

/**
 * Vertical, social-feed adapter for the "Scans near you" screen. Each
 * post renders the scan photo full-width with a diagnosis header and a
 * "Second opinion" action. Both the photo and the action open the
 * community opinion sheet (vote graph + opinions + your own vote) for
 * that scan.
 *
 * Thumbnails load via Glide from /api/community/thumb/<filename>; posts
 * without a thumb_path fall back to a tinted leaf icon so the card stays
 * usable when the server didn't persist a thumbnail.
 */
class NearbyScansAdapter(
    private val displayName: (String) -> String,
    private val onSecondOpinion: (CommunityScanItem) -> Unit,
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

    override fun getItemCount(): Int = items.size

    inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val shimmer: ShimmerFrameLayout = itemView.findViewById(R.id.shimmer_nearby_photo)
        private val photo: ImageView = itemView.findViewById(R.id.iv_nearby_photo)
        private val label: TextView = itemView.findViewById(R.id.tv_nearby_label)
        private val meta: TextView = itemView.findViewById(R.id.tv_nearby_meta)
        private val whenLine: TextView = itemView.findViewById(R.id.tv_nearby_when)
        private val countsPill: View = itemView.findViewById(R.id.ll_nearby_counts)
        private val views: TextView = itemView.findViewById(R.id.tv_nearby_views)
        private val comments: TextView = itemView.findViewById(R.id.tv_nearby_comments)
        private val wantsOpinion: TextView = itemView.findViewById(R.id.tv_nearby_wants_opinion)
        private val opinionBtn: MaterialButton = itemView.findViewById(R.id.btn_nearby_opinion)
        private val avatar: TextView = itemView.findViewById(R.id.tv_nearby_avatar)

        fun bind(item: CommunityScanItem) {
            // Identity line = the author's plant-vibe nickname (the diagnosis
            // moves into the meta). Old rows with no nickname fall back to the
            // disease label so the line is never empty.
            val nick = item.nickname?.takeIf { it.isNotBlank() }
            label.text = nick ?: displayName(item.disease)
            meta.text = buildMeta(item, includeDisease = nick != null)
            // Distance + time live on their own line so a long place name can't
            // ellipsize them away (the whole point of the "near you" feed).
            val whenText = buildWhen(item)
            whenLine.text = whenText
            whenLine.visibility = if (whenText.isEmpty()) View.GONE else View.VISIBLE
            // Avatar derived on-device from the nickname (or the legacy token).
            val seed = nick ?: item.authorToken
            avatar.text =
                if (nick != null) Avatars.monogramForNick(nick) else Avatars.monogramFor(item.authorToken)
            avatar.backgroundTintList =
                android.content.res.ColorStateList.valueOf(Avatars.colorFor(seed))
            wantsOpinion.visibility = if (item.wantsOpinion) View.VISIBLE else View.GONE

            // Views + comments(opinions) overlay on the photo. Each part shows
            // only when its count is non-zero; the pill hides when both are 0.
            views.text = item.viewCount.toString()
            views.visibility = if (item.viewCount > 0) View.VISIBLE else View.GONE
            comments.text = item.opinionCount.toString()
            comments.visibility = if (item.opinionCount > 0) View.VISIBLE else View.GONE
            countsPill.visibility =
                if (item.viewCount > 0 || item.opinionCount > 0) View.VISIBLE else View.GONE

            val click = View.OnClickListener { onSecondOpinion(item) }
            opinionBtn.setOnClickListener(click)
            photo.setOnClickListener(click)

            // ServerConfig.baseUrl ends with `/`; relative path appended.
            val thumb = item.thumbPath
            val url = if (thumb.isNullOrBlank()) {
                null
            } else {
                "${ServerConfig.baseUrl}api/community/thumb/$thumb"
            }
            ThumbLoader.load(shimmer, photo, url)
        }

        /** "Powdery mildew · Warsaw, Poland" — the disease is included only when
         *  the identity line shows the nickname (else it'd repeat). Distance +
         *  time move to [buildWhen] so a long place name can't push them off the
         *  end of this single (ellipsized) line. Missing pieces drop out. */
        private fun buildMeta(item: CommunityScanItem, includeDisease: Boolean): String {
            val locality = item.city.ifBlank { item.region }
            return listOf(
                if (includeDisease) displayName(item.disease) else "",
                formatRegion(locality, countryLabel(item)),
            ).filter { it.isNotEmpty() }.joinToString(" · ")
        }

        /** "≈ 3 km near you · 2h" — the distance (nearby scope only) + the
         *  relative time, on their own always-visible line. */
        private fun buildWhen(item: CommunityScanItem): String =
            listOf(
                formatDistance(item.distanceKm),
                formatTimeAgo(item.createdAt),
            ).filter { it.isNotEmpty() }.joinToString(" · ")

        /** "≈ N km near you" — coords are rounded to ~1 km server-side, so we
         *  floor the display at 1 km rather than show a misleading "0 km".
         *  Empty when the feed had no requester anchor (country/worldwide). */
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
