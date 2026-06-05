package com.plantdiseases.app.ui.guide

import android.text.SpannableStringBuilder
import android.text.style.BackgroundColorSpan
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.data.model.GuideItem
import com.plantdiseases.app.databinding.ItemGuideBinding
import com.plantdiseases.app.util.LocaleHelper

class GuideAdapter(
    private val onClick: (GuideItem) -> Unit,
    var searchQuery: String = ""
) : ListAdapter<GuideItem, GuideAdapter.ViewHolder>(DIFF) {

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

            root.setOnClickListener { onClick(item) }
        }
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
