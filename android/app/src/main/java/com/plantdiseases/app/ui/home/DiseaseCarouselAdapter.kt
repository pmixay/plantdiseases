package com.plantdiseases.app.ui.home

import android.graphics.drawable.Drawable
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DataSource
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.engine.GlideException
import com.bumptech.glide.request.RequestListener
import com.bumptech.glide.request.target.Target
import com.plantdiseases.app.data.GuideDataProvider
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

    inner class VH(val binding: ItemCarouselDiseaseBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemCarouselDiseaseBinding.inflate(
            LayoutInflater.from(parent.context),
            parent,
            false,
        )
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.binding.tvDiseaseName.text = item.title
        holder.binding.tvDiseaseSub.text = item.subtitle
        holder.binding.root.setOnClickListener { onClick(item) }
        bindPhoto(holder, item)
    }

    /** Show the server example photo in the red header, falling back to the
     *  tinted vector when there's no photo / it's offline / the load fails.
     *  A spinner covers the gap while the photo resolves. View state is reset
     *  every bind so a recycled holder never flashes the previous card's photo. */
    private fun bindPhoto(holder: VH, item: Item) {
        val b = holder.binding
        // Baseline: vector shown on top as placeholder, spinner hidden, any prior
        // photo cleared. ivPhoto itself stays visible (transparent when empty) so
        // Glide can measure it — a GONE target never resolves a size and the load
        // would silently never start.
        Glide.with(b.ivPhoto).clear(b.ivPhoto)
        b.ivPhoto.setImageDrawable(null)
        b.ivDisease.setImageResource(item.iconRes)
        b.ivDisease.visibility = View.VISIBLE

        val url = GuideDataProvider.exampleImageUrl(item.guideId)
        if (url == null) {
            b.photoProgress.visibility = View.GONE
            return
        }
        b.photoProgress.visibility = View.VISIBLE
        Glide.with(b.ivPhoto)
            .load(url)
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .centerCrop()
            .listener(object : RequestListener<Drawable> {
                override fun onLoadFailed(
                    e: GlideException?,
                    model: Any?,
                    target: Target<Drawable>,
                    isFirstResource: Boolean,
                ): Boolean {
                    // Keep the vector fallback; just drop the spinner.
                    b.photoProgress.visibility = View.GONE
                    return false
                }

                override fun onResourceReady(
                    resource: Drawable,
                    model: Any,
                    target: Target<Drawable>,
                    dataSource: DataSource,
                    isFirstResource: Boolean,
                ): Boolean {
                    // Photo arrived — drop the spinner and the vector overlay so
                    // the photo (already in ivPhoto, behind them) shows through.
                    b.photoProgress.visibility = View.GONE
                    b.ivDisease.visibility = View.GONE
                    return false
                }
            })
            .into(b.ivPhoto)
    }

    override fun getItemCount(): Int = items.size
}
