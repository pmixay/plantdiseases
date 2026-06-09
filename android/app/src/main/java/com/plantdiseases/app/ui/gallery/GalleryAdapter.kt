package com.plantdiseases.app.ui.gallery

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.databinding.ItemGalleryScanBinding
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import java.io.File

/**
 * RecyclerView adapter for the scan-history gallery. Renders each
 * [ScanHistoryItem] with its localised disease name, relative date,
 * status-coloured accent (healthy / disease / not-a-plant) and a Glide
 * thumbnail. A tap fires [onClick]; a long-press confirms before [onDelete].
 */
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
            val displayName = when {
                item.isNotAPlant -> context.getString(R.string.not_a_plant_diagnosis_name)
                isRu -> item.diseaseNameRu
                else -> item.diseaseName
            }
            tvDiseaseName.text = displayName
            // Per-item label so TalkBack distinguishes thumbnails instead of
            // announcing the same generic string on every card.
            ivPlant.contentDescription = displayName
            tvDate.text = ImageUtils.formatRelativeDate(context, item.timestamp)
            // Confidence on a NAP card is misleading — hide the percentage.
            if (item.isNotAPlant) {
                tvConfidence.text = ""
            } else {
                tvConfidence.text =
                    context.getString(R.string.confidence_format, (item.confidence * 100).toInt())
            }

            val statusColor = when {
                item.isNotAPlant -> R.color.neutral_blue
                item.isHealthy -> R.color.healthy_green
                else -> R.color.disease_red
            }
            statusIndicator.setBackgroundColor(context.getColor(statusColor))
            tvDiseaseName.setTextColor(context.getColor(statusColor))

            // RoundedCorners takes pixels — density-scale 16dp so the photo
            // corners match the card instead of looking near-square on hidpi.
            val cornerPx = (16 * context.resources.displayMetrics.density).toInt()
            Glide.with(context)
                .load(File(item.imagePath))
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .transform(CenterCrop(), RoundedCorners(cornerPx))
                .placeholder(R.drawable.ic_plant_placeholder)
                .into(ivPlant)

            // Opinion-count badge on the user's own shared scans. View counts
            // live on the result screen's community activity line, not here.
            if (item.communityOpinions > 0) {
                tvCommunityBadge.text = context.getString(
                    R.string.gallery_community_badge, item.communityOpinions,
                )
                tvCommunityBadge.visibility = android.view.View.VISIBLE
            } else {
                tvCommunityBadge.visibility = android.view.View.GONE
            }

            // Red "+N" badge + accent stroke when there are opinions the user
            // hasn't opened this scan to see yet. Cleared once they view it.
            if (item.newOpinions > 0) {
                tvNewOpinions.text =
                    context.getString(R.string.gallery_new_opinions_badge, item.newOpinions)
                tvNewOpinions.visibility = android.view.View.VISIBLE
                root.strokeColor = context.getColor(R.color.disease_red)
                root.strokeWidth = (2 * context.resources.displayMetrics.density).toInt()
            } else {
                tvNewOpinions.visibility = android.view.View.GONE
                root.strokeWidth = 0
            }

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
