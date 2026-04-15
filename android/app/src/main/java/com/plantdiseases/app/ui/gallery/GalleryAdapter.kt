package com.plantdiseases.app.ui.gallery

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.databinding.ItemGalleryScanBinding
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import java.io.File

class GalleryAdapter(
    private val onClick: (ScanHistoryItem) -> Unit,
    private val onDelete: (ScanHistoryItem) -> Unit
) : ListAdapter<ScanHistoryItem, GalleryAdapter.ViewHolder>(DIFF_CALLBACK) {

    inner class ViewHolder(val binding: ItemGalleryScanBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemGalleryScanBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = getItem(position)
        val context = holder.binding.root.context
        val isRu = LocaleHelper.isRussian(context)

        holder.binding.apply {
            tvDiseaseName.text = if (isRu) item.diseaseNameRu else item.diseaseName
            tvDate.text = ImageUtils.formatTimestamp(item.timestamp)
            tvConfidence.text = "${(item.confidence * 100).toInt()}%"

            val statusColor = if (item.isHealthy) R.color.healthy_green else R.color.disease_red
            statusIndicator.setBackgroundColor(context.getColor(statusColor))
            tvDiseaseName.setTextColor(context.getColor(statusColor))

            Glide.with(context)
                .load(File(item.imagePath))
                .transform(CenterCrop(), RoundedCorners(16))
                .placeholder(R.drawable.ic_plant_placeholder)
                .into(ivPlant)

            root.setOnClickListener { onClick(item) }
            root.setOnLongClickListener {
                MaterialAlertDialogBuilder(context)
                    .setTitle(R.string.delete_scan)
                    .setMessage(R.string.delete_scan_confirm)
                    .setPositiveButton(R.string.delete) { _, _ -> onDelete(item) }
                    .setNegativeButton(R.string.cancel, null)
                    .show()
                true
            }
        }
    }

    companion object {
        private val DIFF_CALLBACK = object : DiffUtil.ItemCallback<ScanHistoryItem>() {
            override fun areItemsTheSame(old: ScanHistoryItem, new: ScanHistoryItem) = old.id == new.id
            override fun areContentsTheSame(old: ScanHistoryItem, new: ScanHistoryItem) = old == new
        }
    }
}
