package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Owns "add this scan to a garden plant": picking an existing plant or creating
 * a new one from the scan, and persisting the attachment. The CreatePlantSheet
 * save arrives via the Fragment Result API (registered in onCreate) so it
 * survives the activity + sheet being recreated together.
 */
class GardenAttachController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
) : KoinComponent {
    private val plantRepository: PlantRepository by inject()

    fun registerCreatePlantListener() {
        activity.supportFragmentManager.setFragmentResultListener(
            CreatePlantSheet.REQUEST_KEY,
            activity,
        ) { _, result -> onCreatePlantResult(result) }
    }

    fun showAddToGardenDialog(scan: ScanEntity) {
        activity.lifecycleScope.launch {
            val plants = plantRepository.getPlants()
            if (plants.isEmpty()) {
                showCreatePlantForScanDialog(scan)
                return@launch
            }

            val labels = plants.map { it.plant.name }.toTypedArray()
            MaterialAlertDialogBuilder(activity)
                .setTitle(R.string.add_to_garden)
                .setItems(labels) { _, which ->
                    activity.lifecycleScope.launch {
                        plantRepository.assignScanToPlant(scan.id, plants[which].plant.id)
                        binding.btnAddToGarden.text = activity.getString(R.string.added_to_garden)
                        Toast.makeText(
                            activity,
                            activity.getString(R.string.scan_added_to_garden),
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
                .setPositiveButton(R.string.create_new_plant) { _, _ ->
                    showCreatePlantForScanDialog(scan)
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
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
        activity.lifecycleScope.launch {
            val plantId = plantRepository.savePlant(
                name = plantName,
                species = result.getString(CreatePlantSheet.RESULT_SPECIES),
                scientificName = result.getString(CreatePlantSheet.RESULT_SCIENTIFIC),
                commonName = result.getString(CreatePlantSheet.RESULT_COMMON),
            )
            plantRepository.assignScanToPlant(scanId, plantId)
            binding.btnAddToGarden.text = activity.getString(R.string.added_to_garden)
            Toast.makeText(
                activity,
                activity.getString(R.string.scan_added_to_garden),
                Toast.LENGTH_SHORT,
            ).show()
        }
    }
}
