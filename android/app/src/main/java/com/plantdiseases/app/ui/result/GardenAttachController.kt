package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.NestedScrollView
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DecodeFormat
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.common.dpx
import com.plantdiseases.app.ui.common.sectionRow
import com.plantdiseases.app.util.InAppReviewManager
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch

/**
 * Owns "add this scan to a garden plant": picking an existing plant or creating
 * a new one from the scan, and persisting the attachment. The CreatePlantSheet
 * save arrives via the Fragment Result API (registered in onCreate) so it
 * survives the activity + sheet being recreated together.
 */
class GardenAttachController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
    private val plantRepository: PlantRepository,
    private val reviewManager: InAppReviewManager,
) {

    fun registerCreatePlantListener() {
        activity.supportFragmentManager.setFragmentResultListener(
            CreatePlantSheet.REQUEST_KEY,
            activity,
        ) { _, result -> onCreatePlantResult(result) }
    }

    fun showAddToGardenDialog(scan: ScanEntity) {
        activity.lifecycleScope.launch {
            // Name + photo picker — getPlants() would run the full garden
            // summary (journal, reminders, plans, health verdict) per plant
            // and throw all of it away.
            val plants = plantRepository.getPlantsForPicker()
            if (plants.isEmpty()) {
                showCreatePlantForScanDialog(scan)
                return@launch
            }

            lateinit var dialog: AlertDialog
            val column = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(activity.dpx(20), activity.dpx(4), activity.dpx(20), activity.dpx(4))
            }
            column.addView(
                TextView(activity).apply {
                    text = activity.getString(R.string.add_to_garden_hint)
                    textSize = 13f
                    setTextColor(activity.getColor(R.color.on_surface_secondary))
                    setPadding(0, 0, 0, activity.dpx(12))
                },
            )
            plants.forEach { item ->
                // Only pass a thumb loader when there's a real photo — sectionRow
                // skips its own icon padding/tint whenever thumb is non-null (a
                // photo needs neither), so a plant with no photo must get
                // thumb = null and fall through to sectionRow's own tinted
                // fallback icon rather than an untinted ic_garden that renders
                // as a near-invisible white outline on the pale icon-circle bg.
                val thumbSource = item.thumbnailSource
                column.addView(
                    activity.sectionRow(
                        title = item.name,
                        subtitle = null,
                        iconRes = R.drawable.ic_garden,
                        thumb = thumbSource?.let { source -> { iv: ImageView -> bindPlantThumb(iv, source) } },
                        onClick = {
                            attachScanToPlant(scan, item.id)
                            dialog.dismiss()
                        },
                    ),
                )
            }

            dialog = MaterialAlertDialogBuilder(activity)
                .setTitle(R.string.add_to_garden)
                .setView(NestedScrollView(activity).apply { addView(column) })
                .setPositiveButton(R.string.create_new_plant) { _, _ ->
                    showCreatePlantForScanDialog(scan)
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
        }
    }

    private fun bindPlantThumb(imageView: ImageView, source: String) {
        Glide.with(activity)
            .load(source)
            .format(DecodeFormat.PREFER_RGB_565)
            .transform(CenterCrop(), RoundedCorners(activity.dpx(17)))
            .placeholder(R.drawable.ic_garden)
            .error(R.drawable.ic_garden)
            .into(imageView)
    }

    private fun attachScanToPlant(scan: ScanEntity, plantId: Long) {
        activity.lifecycleScope.launch {
            plantRepository.assignScanToPlant(scan.id, plantId)
            onAddedToGarden()
        }
    }

    private fun showCreatePlantForScanDialog(scan: ScanEntity) {
        // Suggested name: prefer the detected species' common name (people name
        // plants after the plant, not its disease); fall back to the LOCALIZED
        // disease name — the raw English diseaseName used to leak into RU here.
        val isRu = LocaleHelper.isRussian(activity)
        val suggestedName = scan.speciesCommon?.takeIf { it.isNotBlank() }
            ?: if (isRu) scan.diseaseNameRu else scan.diseaseName
        CreatePlantSheet.show(activity.supportFragmentManager, scan, initialName = suggestedName)
    }

    private fun onCreatePlantResult(result: Bundle) {
        val plantName = result.getString(CreatePlantSheet.RESULT_NAME) ?: return
        val scanId = result.getLong(CreatePlantSheet.RESULT_SCAN_ID, -1L)
        if (scanId <= 0L) return
        val roomId = result.getLong(CreatePlantSheet.RESULT_ROOM_ID, CreatePlantSheet.RESULT_ROOM_ID_UNASSIGNED)
            .takeIf { it != CreatePlantSheet.RESULT_ROOM_ID_UNASSIGNED }
        activity.lifecycleScope.launch {
            val plantId = plantRepository.savePlant(
                name = plantName,
                species = result.getString(CreatePlantSheet.RESULT_SPECIES),
                scientificName = result.getString(CreatePlantSheet.RESULT_SCIENTIFIC),
                commonName = result.getString(CreatePlantSheet.RESULT_COMMON),
                roomId = roomId,
            )
            plantRepository.assignScanToPlant(scanId, plantId)
            onAddedToGarden()
        }
    }

    /** Common tail of both attach paths (pick-existing and create-new): the
     *  confirmation UI, and — since organizing a scan into a garden plant is a
     *  genuine engaged-user moment — a chance at RuStore's review prompt. */
    private fun onAddedToGarden() {
        binding.btnAddToGarden.text = activity.getString(R.string.added_to_garden)
        Toast.makeText(
            activity,
            activity.getString(R.string.scan_added_to_garden),
            Toast.LENGTH_SHORT,
        ).show()
        reviewManager.maybeRequestReview()
    }
}
