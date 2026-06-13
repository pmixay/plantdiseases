package com.plantdiseases.app.ui.home

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.ImageView
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.TrendingSpecies
import com.plantdiseases.app.databinding.ItemCarouselSpeciesBinding

/**
 * Horizontal carousel of trending species. Photos are fetched lazily per
 * card by [loadImage] (HomeFragment resolves a Wikipedia lead photo via
 * /api/species-info), so the list shows names + counts immediately and the
 * images fill in as they arrive.
 */
class SpeciesCarouselAdapter(
    private val onClick: (TrendingSpecies) -> Unit,
    private val loadImage: (scientificName: String, target: ImageView) -> Unit,
) : RecyclerView.Adapter<SpeciesCarouselAdapter.VH>() {

    private var items: List<TrendingSpecies> = emptyList()

    fun submit(list: List<TrendingSpecies>) {
        items = list
        notifyDataSetChanged()
    }

    inner class VH(val binding: ItemCarouselSpeciesBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemCarouselSpeciesBinding.inflate(
            LayoutInflater.from(parent.context), parent, false,
        )
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        val ctx = holder.binding.root.context
        holder.binding.tvSpeciesName.text = item.species
        holder.binding.tvSpeciesCount.text =
            ctx.resources.getQuantityString(R.plurals.home_species_count, item.count, item.count)
        holder.binding.ivSpecies.setImageResource(R.drawable.ic_plant_placeholder)
        loadImage(item.species, holder.binding.ivSpecies)
        holder.binding.root.setOnClickListener { onClick(item) }
    }

    override fun getItemCount(): Int = items.size
}
