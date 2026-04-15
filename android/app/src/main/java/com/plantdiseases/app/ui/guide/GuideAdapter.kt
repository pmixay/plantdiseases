package com.plantdiseases.app.ui.guide

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
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
            root.setOnClickListener { onClick(item) }
        }
    }

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<GuideItem>() {
            override fun areItemsTheSame(a: GuideItem, b: GuideItem) = a.id == b.id
            override fun areContentsTheSame(a: GuideItem, b: GuideItem) = a == b
        }
    }
}
