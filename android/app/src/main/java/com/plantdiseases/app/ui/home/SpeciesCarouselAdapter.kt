package com.plantdiseases.app.ui.home

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.ImageView
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.databinding.ItemCarouselSpeciesBinding

/**
 * Horizontal carousel of species cards. Photos are fetched lazily per card by
 * [loadImage] (HomeFragment resolves a Wikipedia lead photo via
 * /api/species-info keyed off the scientific name), so the list shows
 * titles + subtitles immediately and the images fill in as they arrive.
 *
 * Feeds two ways: trending species near the user, and — when there is no
 * trending data — a bundled "popular houseplants" fallback. The [Item] carries
 * the [scientific] name separately from the displayed [title] so the fallback
 * can show common names while still keying the photo/detail lookup off the
 * scientific name.
 */
class SpeciesCarouselAdapter(
    private val onClick: (Item) -> Unit,
    private val loadImage: (scientificName: String, target: ImageView) -> Unit,
) : RecyclerView.Adapter<SpeciesCarouselAdapter.VH>() {

    data class Item(
        val scientific: String,
        val title: String,
        val subtitle: String,
    )

    private var items: List<Item> = emptyList()

    fun submit(list: List<Item>) {
        items = list
        notifyDataSetChanged()
    }

    inner class VH(val binding: ItemCarouselSpeciesBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemCarouselSpeciesBinding.inflate(
            LayoutInflater.from(parent.context),
            parent,
            false,
        )
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.binding.tvSpeciesName.text = item.title
        holder.binding.tvSpeciesCount.text = item.subtitle
        // Blank subtitle (placeholder / sub-1% share) → hide, no empty gap.
        holder.binding.tvSpeciesCount.visibility =
            if (item.subtitle.isBlank()) android.view.View.GONE else android.view.View.VISIBLE
        holder.binding.ivSpecies.setImageResource(
            com.plantdiseases.app.R.drawable.ic_plant_placeholder,
        )
        loadImage(item.scientific, holder.binding.ivSpecies)
        holder.binding.root.setOnClickListener { onClick(item) }
    }

    override fun getItemCount(): Int = items.size
}
