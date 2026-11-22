package com.plantdiseases.app.ui.community

import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.facebook.shimmer.ShimmerFrameLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.ThumbLoader

/**
 * Single-column, scrolling-forum adapter for the community feed (Community
 * v2 — replaced the staggered photo grid). Each post renders the scan photo
 * full-width under the author's nickname/place/time header, then a like +
 * comments + views action row. The model's diagnosis is intentionally NOT on
 * the card — it would anchor voters before they look at the photo; it
 * appears inside the opinion sheet, which the photo and the comments/views
 * rows all open. Liking is handled here directly, optimistically, via
 * [onLike] + [updateLike] rather than round-tripping through the sheet.
 *
 * Thumbnails load via Glide from /api/community/thumb/<filename>; posts
 * without a thumb_path fall back to a tinted leaf icon so the card stays
 * usable when the server didn't persist a thumbnail.
 */
class NearbyScansAdapter(
    private val onOpen: (CommunityScanItem) -> Unit,
    private val onAuthor: (CommunityScanItem) -> Unit,
    private val onLike: (CommunityScanItem) -> Unit,
    private val onComments: (CommunityScanItem) -> Unit = onOpen,
) : RecyclerView.Adapter<NearbyScansAdapter.VH>() {

    /** A guest's like and comment actions are drawn locked (gray); their taps
     *  still reach [onLike] and [onComments], which offer sign-in. */
    var guest: Boolean = false
        set(value) {
            if (field == value) return
            field = value
            notifyDataSetChanged()
        }

    private var items: MutableList<CommunityScanItem> = mutableListOf()

    fun submit(newItems: List<CommunityScanItem>) {
        val old = items
        val added = newItems.size - old.size
        // Cursor paging appends: the new list is the old one plus a tail, so
        // only the tail is actually new — rebinding (and re-Glide-ing) every
        // visible photo mid-fling is pure waste. Anything else (a server sort
        // genuinely replacing the feed) still needs a full
        // invalidation.
        val isAppend = added > 0 && old.indices.all { old[it] == newItems[it] }
        items = newItems.toMutableList()
        if (isAppend) notifyItemRangeInserted(old.size, added) else notifyDataSetChanged()
    }

    /** Patch one row's like state in place (optimistic update + the eventual
     *  server-confirmed value) without reloading the whole list. */
    fun updateLike(scanId: String, likeCount: Int, likedByMe: Boolean) {
        val index = items.indexOfFirst { it.scanId == scanId }
        if (index < 0) return
        items[index] = items[index].copy(likeCount = likeCount, likedByMe = likedByMe)
        notifyItemChanged(index, PAYLOAD_LIKE)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_nearby_scan, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) = holder.bind(items[position])

    /** A like tap only moves a 20dp heart and a count. Without a payload the
     *  default item animator refuses to reuse the holder, so RecyclerView binds
     *  a SECOND one and re-issues the 260dp photo load — twice per tap
     *  (optimistic + server confirmation). */
    override fun onBindViewHolder(holder: VH, position: Int, payloads: MutableList<Any>) {
        if (payloads.contains(PAYLOAD_LIKE)) {
            holder.bindLike(items[position])
        } else {
            super.onBindViewHolder(holder, position, payloads)
        }
    }

    /** Release the pooled holder's bitmap instead of pinning a 260dp photo
     *  in every off-screen holder. */
    override fun onViewRecycled(holder: VH) {
        super.onViewRecycled(holder)
        holder.clearPhoto()
    }

    override fun getItemCount(): Int = items.size

    fun itemAt(position: Int): CommunityScanItem? = items.getOrNull(position)

    inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val shimmer: ShimmerFrameLayout = itemView.findViewById(R.id.shimmer_nearby_photo)
        private val photo: ImageView = itemView.findViewById(R.id.iv_nearby_photo)
        private val label: TextView = itemView.findViewById(R.id.tv_nearby_label)
        private val meta: TextView = itemView.findViewById(R.id.tv_nearby_meta)
        private val whenLine: TextView = itemView.findViewById(R.id.tv_nearby_when)
        private val likeRow: View = itemView.findViewById(R.id.ll_nearby_like)
        private val likeIcon: ImageView = itemView.findViewById(R.id.iv_nearby_like)
        private val likes: TextView = itemView.findViewById(R.id.tv_nearby_likes)
        private val commentsRow: View = itemView.findViewById(R.id.ll_nearby_comments)
        private val commentsIcon: ImageView = itemView.findViewById(R.id.iv_nearby_comments)
        private val views: TextView = itemView.findViewById(R.id.tv_nearby_views)
        private val comments: TextView = itemView.findViewById(R.id.tv_nearby_comments)
        private val wantsOpinion: TextView = itemView.findViewById(R.id.tv_nearby_wants_opinion)
        private val avatar: TextView = itemView.findViewById(R.id.tv_nearby_avatar)

        fun bind(item: CommunityScanItem) {
            // Identity line = the author's plant-vibe nickname. Old rows with
            // no nickname fall back to a generic localized handle — never the
            // diagnosis, which stays off the card.
            val nick = item.nickname?.takeIf { it.isNotBlank() }
            label.text = nick ?: itemView.context.getString(R.string.community_anonymous_author)
            meta.text = buildMeta(item)
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
            val openAuthor = View.OnClickListener { onAuthor(item) }
            avatar.setOnClickListener(openAuthor)
            label.setOnClickListener(openAuthor)
            wantsOpinion.visibility = if (item.wantsOpinion) View.VISIBLE else View.GONE

            // Always show all three counts, zero included — hiding a count at
            // zero was why the same action row looked different from post to
            // post ("chaotic"): a like/comment landing on a post made a pill
            // pop into existence that wasn't there a moment before.
            views.text = item.viewCount.toString()
            if (item.wantsOpinion) {
                comments.text = item.opinionCount.toString()
                commentsIcon.setImageResource(R.drawable.ic_comment)
                commentsRow.alpha = 1f
                commentsRow.contentDescription = itemView.context.getString(
                    R.string.community_open_replies,
                    item.opinionCount,
                )
            } else {
                comments.text = itemView.context.getString(R.string.community_replies_locked_short)
                commentsIcon.setImageResource(R.drawable.ic_lock)
                commentsRow.alpha = 0.55f
                commentsRow.contentDescription = itemView.context.getString(
                    R.string.community_replies_locked_readable,
                )
            }
            bindLike(item)

            val open = View.OnClickListener { onOpen(item) }
            photo.setOnClickListener(open)
            commentsRow.setOnClickListener { onComments(item) }
            val context = itemView.context
            commentsIcon.imageTintList = android.content.res.ColorStateList.valueOf(
                ContextCompat.getColor(context, if (guest) R.color.guest_locked else R.color.on_surface_secondary),
            )
            comments.setTextColor(
                ContextCompat.getColor(context, if (guest) R.color.guest_locked else R.color.on_surface),
            )

            // ServerConfig.appBaseUrl ends with `/`; relative path appended.
            val thumb = item.thumbPath
            val url = if (thumb.isNullOrBlank()) {
                null
            } else {
                "${ServerConfig.appBaseUrl}api/community/thumb/$thumb"
            }
            ThumbLoader.load(shimmer, photo, url)
        }

        /** The heart + count + its click target — the only thing a like tap
         *  changes, so it is also the whole [PAYLOAD_LIKE] rebind. The listener
         *  is re-set because [onLike] needs the CURRENT counts as the value to
         *  roll back to when the request fails. */
        fun bindLike(item: CommunityScanItem) {
            likes.text = item.likeCount.toString()
            likeIcon.setImageResource(
                if (item.likedByMe) R.drawable.ic_heart_filled else R.drawable.ic_heart,
            )
            val color = ContextCompat.getColor(
                itemView.context,
                when {
                    guest -> R.color.guest_locked
                    item.likedByMe -> R.color.primary
                    else -> R.color.on_surface
                },
            )
            likeIcon.imageTintList = android.content.res.ColorStateList.valueOf(color)
            likes.setTextColor(
                if (guest) color else ContextCompat.getColor(itemView.context, R.color.on_surface),
            )
            likeRow.setOnClickListener { onLike(item) }
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

    private companion object {
        /** Partial-rebind marker for a like toggle — see the payload overload
         *  of [onBindViewHolder]. */
        const val PAYLOAD_LIKE = "like"
    }
}
