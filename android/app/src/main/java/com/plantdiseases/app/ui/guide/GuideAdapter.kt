package com.plantdiseases.app.ui.guide

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
    private val onClick: (GuideItem) -> Unit
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
            tvTitle.text = if (isRu) item.titleRu else item.titleEn
            tvDescription.text = if (isRu) item.descriptionRu else item.descriptionEn
            ivIcon.setImageResource(item.iconRes)

            // Set unique background and tint per category
            val (bgRes, tintColor) = getCategoryStyle(item.category)
            ivIcon.setBackgroundResource(bgRes)
            ivIcon.setColorFilter(ctx.getColor(tintColor))

            root.setOnClickListener { onClick(item) }
        }
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
