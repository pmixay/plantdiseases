package com.plantdiseases.app.ui.guide

import android.text.SpannableStringBuilder
import android.text.style.BackgroundColorSpan
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.data.model.GuideItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ItemGuideBinding
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

class GuideAdapter(
    private val onClick: (GuideItem) -> Unit,
    var searchQuery: String = "",
    // Supplied by the host fragment so disease/pest rows can load a real
    // Wikipedia example photo (same source as the result screen). Null in
    // contexts that don't need photos (the rows then just show the icon).
    private val scope: CoroutineScope? = null,
    private val scanRepository: ScanRepository? = null,
) : ListAdapter<GuideItem, GuideAdapter.ViewHolder>(DIFF) {

    // guideId -> resolved photo URL; value null means "fetched, none found".
    // Caches across binds so scrolling doesn't refetch (and never flickers).
    private val photoCache = mutableMapOf<String, String?>()

    inner class ViewHolder(val binding: ItemGuideBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemGuideBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = getItem(position)
        val ctx = holder.binding.root.context
        val isRu = LocaleHelper.isRussian(ctx)

        holder.binding.apply {
            val title = if (isRu) item.titleRu else item.titleEn
            val desc = if (isRu) item.descriptionRu else item.descriptionEn

            // Highlight search matches
            if (searchQuery.length >= 2) {
                val highlightColor = ctx.getColor(R.color.search_highlight)
                tvTitle.text = highlightText(title, searchQuery, highlightColor)
                tvDescription.text = highlightText(desc, searchQuery, highlightColor)
            } else {
                tvTitle.text = title
                tvDescription.text = desc
            }

            ivIcon.setImageResource(item.iconRes)

            // Set unique background and tint per category
            val (bgRes, tintColor) = getCategoryStyle(item.category)
            ivIcon.setBackgroundResource(bgRes)
            ivIcon.setColorFilter(ctx.getColor(tintColor))

            bindPhoto(ivPhoto, item)

            root.setOnClickListener { onClick(item) }
        }
    }

    /**
     * Lazily load a real example photo (Wikipedia) for disease & pest rows,
     * mirroring the result screen's "What it looks like" strip. Care topics
     * have no photo and stay icon-only. Recycling-safe: the ImageView is
     * tagged with the item id so a late network result for a since-recycled
     * row is dropped instead of painting the wrong card.
     */
    private fun bindPhoto(iv: ImageView, item: GuideItem) {
        // Local copies so the non-null smart-cast survives into the lambda.
        val s = scope
        val repo = scanRepository
        val query = GuideDataProvider.wikiQueryForGuideId(item.id)
        val eligible = query != null &&
            (item.category == GuideCategory.COMMON_DISEASES ||
                item.category == GuideCategory.PESTS)
        if (!eligible || s == null || repo == null) {
            iv.tag = null
            iv.visibility = View.GONE
            Glide.with(iv).clear(iv)
            return
        }

        iv.tag = item.id
        if (photoCache.containsKey(item.id)) {
            applyPhoto(iv, photoCache[item.id])
            return
        }

        // Unknown yet — hide until resolved, then cache the result.
        iv.visibility = View.GONE
        Glide.with(iv).clear(iv)
        s.launch {
            val url = repo.getSpeciesInfo(query!!, "en").getOrNull()?.let { info ->
                info.imageUrl?.takeIf { it.isNotBlank() }
                    ?: info.images.firstOrNull { it.isNotBlank() }
            }
            photoCache[item.id] = url
            if (iv.tag == item.id) applyPhoto(iv, url)
        }
    }

    private fun applyPhoto(iv: ImageView, url: String?) {
        if (url.isNullOrBlank()) {
            iv.visibility = View.GONE
            Glide.with(iv).clear(iv)
            return
        }
        iv.visibility = View.VISIBLE
        val radiusPx = (12 * iv.resources.displayMetrics.density).toInt()
        Glide.with(iv)
            .load(url)
            .transform(CenterCrop(), RoundedCorners(radiusPx))
            .into(iv)
    }

    private fun highlightText(text: String, query: String, color: Int): CharSequence {
        if (query.isBlank()) return text
        val ssb = SpannableStringBuilder(text)
        val lowerText = text.lowercase()
        val lowerQuery = query.lowercase()
        var start = lowerText.indexOf(lowerQuery)
        while (start >= 0) {
            ssb.setSpan(
                BackgroundColorSpan(color),
                start,
                start + query.length,
                SpannableStringBuilder.SPAN_EXCLUSIVE_EXCLUSIVE
            )
            start = lowerText.indexOf(lowerQuery, start + query.length)
        }
        return ssb
    }

    private fun getCategoryStyle(category: GuideCategory): Pair<Int, Int> {
        return when (category) {
            GuideCategory.COMMON_DISEASES -> R.drawable.bg_category_diseases to R.color.disease_red
            GuideCategory.PESTS -> R.drawable.bg_category_pests to R.color.warning_amber
            GuideCategory.WATERING -> R.drawable.bg_category_watering to R.color.watering_blue
            GuideCategory.LIGHTING -> R.drawable.bg_category_lighting to R.color.lighting_yellow
            GuideCategory.CARE_TIPS -> R.drawable.bg_category_care to R.color.healthy_green
        }
    }

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<GuideItem>() {
            override fun areItemsTheSame(a: GuideItem, b: GuideItem) = a.id == b.id
            override fun areContentsTheSame(a: GuideItem, b: GuideItem) = a == b
        }
    }
}
