package com.plantdiseases.app.ui.result

import android.util.TypedValue
import android.view.View
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.util.InAppReviewManager
import kotlinx.coroutines.launch

/**
 * Owns the server-side care-plan card (/api/care-plan): inserts it after the
 * AI Plant Doctor action, fetches a species-specific plan (or a locally
 * computed one for offline Aqua scans), and hands an accepted plan off to the
 * reminder scheduler.
 */
class CarePlanController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
    private val scanRepository: ScanRepository,
    private val plantRepository: PlantRepository,
    private val reviewManager: InAppReviewManager,
) {
    private var card: CarePlanCardView? = null

    fun bind(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        if (repository.isAbstainedScan(scan)) {
            card?.visibility = View.GONE
            return
        }
        val container = binding.contentLayout
        val classKey = repository.topClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')

        // Reuse a single card across re-binds, inserted just after the
        // doctor / second-opinion action row so identity and the follow-up
        // entry points remain above the longer care details.
        val view = card ?: CarePlanCardView(activity).also { fresh ->
            // Keyed to the diagnosis, not the species baseline.
            fresh.isCurePlan = true
            card = fresh
            fresh.layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dpToPx() }
            val anchorIdx = container.indexOfChild(binding.resultActionRow)
                .takeIf { it >= 0 } ?: (container.childCount - 1)
            container.addView(fresh, anchorIdx + 1)
        }
        view.visibility = View.VISIBLE

        // Offline (Aqua) scans never call the server: bind a basic,
        // locally-computed plan instead, visually marked as offline.
        if ("offline_aqua" in repository.parseWarnings(scan.warnings)) {
            view.bind(
                plan = com.plantdiseases.app.util.OfflineScanContent.localCarePlan(classKey, isRu),
                speciesDisplay = null,
                showApplyToReminders = scan.plantId != null,
                onApplyToReminders = { applied -> applyToReminders(scan, applied) },
                offlineBasic = true,
            )
            return
        }

        view.showLoading(null)

        activity.lifecycleScope.launch {
            // Prefer the species attached to the saved garden plant; otherwise
            // fall back to the PlantNet species detected for this scan so the
            // care plan is species-specific even before "Add to garden".
            val plant = scan.plantId?.let { plantRepository.getPlant(it) }
            val sciName = plant?.scientificName?.takeIf { it.isNotBlank() }
                ?: scan.speciesScientific?.takeIf { it.isNotBlank() }
            val commonName = plant?.commonName?.takeIf { it.isNotBlank() }
                ?: plant?.species?.takeIf { it.isNotBlank() }
                ?: scan.speciesCommon?.takeIf { it.isNotBlank() }
            // Server lookup wants the most precise species (scientific first), raw.
            val plantSpecies = sciName ?: commonName
            // Card display: localized — never an English common name for a RU user.
            val speciesDisplay =
                com.plantdiseases.app.util.localizedSpeciesName(sciName, commonName, isRu) ?: plantSpecies
            view.showLoading(speciesDisplay)

            val request = CarePlanRequest(
                disease = classKey,
                species = plantSpecies,
                light = "bright_indirect",
                locale = if (isRu) "ru" else "en",
            )
            scanRepository.fetchCarePlan(request).onSuccess { plan ->
                view.bind(
                    plan = plan,
                    speciesDisplay = speciesDisplay,
                    showApplyToReminders = scan.plantId != null,
                    onApplyToReminders = { applied -> applyToReminders(scan, applied) },
                )
            }.onFailure {
                view.showError(onRetry = { bind(scan, repository, isRu) })
            }
        }
    }

    private fun applyToReminders(
        scan: ScanEntity,
        plan: com.plantdiseases.app.data.model.CarePlanResponse,
    ) {
        val plantId = scan.plantId ?: return
        activity.lifecycleScope.launch {
            val plant = plantRepository.getPlant(plantId) ?: return@launch
            com.plantdiseases.app.ui.garden.ReminderScheduleSheet.showWithCarePlanDefaults(
                activity.supportFragmentManager,
                plant,
                plan,
            )
            Toast.makeText(
                activity,
                activity.getString(R.string.care_plan_reminders_applied),
                Toast.LENGTH_SHORT,
            ).show()
            // Turning a care plan into scheduled reminders is another genuine
            // engaged-user moment — see InAppReviewManager for the shared cooldown.
            reviewManager.maybeRequestReview()
        }
    }

    private fun Int.dpToPx(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        activity.resources.displayMetrics,
    ).toInt()
}
