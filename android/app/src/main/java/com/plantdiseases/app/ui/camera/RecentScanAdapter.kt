package com.plantdiseases.app.ui.camera

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.databinding.ItemRecentScanBinding
import com.plantdiseases.app.util.LocaleHelper
import java.io.File

class RecentScanAdapter(
    private val items: List<ScanHistoryItem>,
    private val onClick: (ScanHistoryItem) -> Unit
) : RecyclerView.Adapter<RecentScanAdapter.ViewHolder>() {

    inner class ViewHolder(val binding: ItemRecentScanBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemRecentScanBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        val context = holder.binding.root.context
        val isRu = LocaleHelper.isRussian(context)

        holder.binding.apply {
            tvDiseaseName.text = if (isRu) item.diseaseNameRu else item.diseaseName

            val statusColor = if (item.isHealthy) {
                R.color.healthy_green
            } else {
                R.color.disease_red
            }
            tvDiseaseName.setTextColor(context.getColor(statusColor))

            Glide.with(context)
                .load(File(item.imagePath))
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .transform(CenterCrop(), RoundedCorners(24))
                .placeholder(R.drawable.ic_plant_placeholder)
                .into(ivThumbnail)

            root.setOnClickListener { onClick(item) }
        }
    }

    override fun getItemCount() = items.size
}
