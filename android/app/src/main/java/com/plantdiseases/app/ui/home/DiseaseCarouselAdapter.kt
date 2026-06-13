package com.plantdiseases.app.ui.home

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.databinding.ItemCarouselDiseaseBinding

/**
 * Horizontal carousel of disease cards, shared by the Home "Popular
 * diseases" and "Disease guide" rows. Each card carries the guide doc id it
 * opens on tap (see [DiseaseCarouselAdapter.Item]).
 */
class DiseaseCarouselAdapter(
    private val onClick: (Item) -> Unit,
) : RecyclerView.Adapter<DiseaseCarouselAdapter.VH>() {

    /** iconRes + localized title + subtitle (a scan count for "popular", or
     *  a "Guide" tag for the documentation row), plus the guide id to open. */
    data class Item(
        val guideId: String,
        val iconRes: Int,
        val title: String,
        val subtitle: String,
    )

    private var items: List<Item> = emptyList()

    fun submit(list: List<Item>) {
        items = list
        notifyDataSetChanged()
    }

    inner class VH(val binding: ItemCarouselDiseaseBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemCarouselDiseaseBinding.inflate(
            LayoutInflater.from(parent.context), parent, false,
        )
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.binding.ivDisease.setImageResource(item.iconRes)
        holder.binding.tvDiseaseName.text = item.title
        holder.binding.tvDiseaseSub.text = item.subtitle
        holder.binding.root.setOnClickListener { onClick(item) }
    }

    override fun getItemCount(): Int = items.size
}
