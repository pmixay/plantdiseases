package com.plantdiseases.app.ui.result

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.util.ServerConfig

/**
 * Horizontal carousel adapter for the "Similar scans near you" card.
 *
 * Each cell shows a 96dp thumbnail (loaded via Glide from
 * /api/community/thumb/<filename>) plus the canonical disease label.
 * Cells with no thumb_path fall back to a colored disease icon so
 * the card stays usable when the server didn't persist a thumbnail.
 */
class SimilarScansAdapter(
    private var items: List<CommunityScanItem> = emptyList(),
    private val displayName: (String) -> String,
) : RecyclerView.Adapter<SimilarScansAdapter.VH>() {

    fun submit(newItems: List<CommunityScanItem>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_similar_scan, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class VH(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val image: ImageView = itemView.findViewById(R.id.iv_similar_thumb)
        private val label: TextView = itemView.findViewById(R.id.tv_similar_label)

        fun bind(item: CommunityScanItem) {
            label.text = displayName(item.disease)
            val thumb = item.thumbPath
            if (thumb.isNullOrBlank()) {
                image.setImageResource(R.drawable.ic_disease)
            } else {
                // ServerConfig.baseUrl ends with `/`; relative path
                // appended here.
                val url = "${ServerConfig.baseUrl}api/community/thumb/$thumb"
                Glide.with(itemView)
                    .load(url)
                    .placeholder(R.drawable.ic_disease)
                    .error(R.drawable.ic_disease)
                    .centerCrop()
                    .into(image)
            }
        }
    }
}
