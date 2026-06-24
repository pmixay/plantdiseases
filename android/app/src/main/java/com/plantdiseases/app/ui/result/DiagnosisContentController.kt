package com.plantdiseases.app.ui.result

import android.animation.ValueAnimator
import android.graphics.Typeface
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch

/**
 * Renders the static diagnosis body: the pipeline-warning banner, the
 * alternative-diagnoses bars, the treatment/prevention tabs (with the AI plant
 * doctor follow-up), the pet-toxicity card and the bundled example photo of
 * the diagnosed problem. Pure view binding off a [ScanEntity] — no community
 * or feedback state lives here.
 */
class DiagnosisContentController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
) {
    private fun displayName(key: String, isRu: Boolean): String? =
        DiseaseLabels.displayName(key, isRu)

    fun bindPipelineWarnings(scan: ScanEntity, repository: ScanRepository) {
        val warnings = repository.parseWarnings(scan.warnings)
        if (warnings.isEmpty()) {
            binding.cardPipelineWarning.visibility = View.GONE
            return
        }

        val isRu = LocaleHelper.isRussian(activity)

        // The server can attach a dynamic disease_suspected:<class> warning
        // alongside a healthy verdict — match by prefix and surface the
        // suspected class name (an exact-string `in` check could never match).
        val suspectedKey = warnings
            .firstOrNull { it.startsWith("disease_suspected:") }
            ?.substringAfter(":")

        // Priority order: very_low_confidence > blur > not-a-leaf gate >
        // weak-healthy reroute > uncertain_healthy / disease_suspected (soft
        // hedge) > low_confidence (mild hedge).
        binding.cardPipelineWarning.visibility = View.VISIBLE
        when {
            // On-device (Aqua) scan — always caveat: the offline model has no
            // detector / post-processing pipeline behind it.
            "offline_aqua" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_offline_scan_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_offline_scan)
            }
            "very_low_confidence" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_very_low_confidence_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_very_low_confidence)
            }
            "blurry_image" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_blurry_image_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_blurry_image)
            }
            "nap_ood_gated" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_nap_ood_gated_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_nap_ood_gated)
            }
            "weak_healthy_rerouted" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_weak_healthy_rerouted_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_weak_healthy_rerouted)
            }
            suspectedKey != null -> {
                val label = displayName(suspectedKey, isRu) ?: suspectedKey
                binding.tvPipelineWarningTitle.setText(R.string.warning_disease_suspected_title)
                binding.tvPipelineWarningBody.text =
                    activity.getString(R.string.warning_disease_suspected, label)
            }
            "uncertain_healthy" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_uncertain_healthy_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_uncertain_healthy)
            }
            "viral_variegation_caveat" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_viral_variegation_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_viral_variegation)
            }
            "low_confidence" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_low_confidence_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_low_confidence)
            }
            else -> {
                binding.cardPipelineWarning.visibility = View.GONE
            }
        }
    }

    fun bindAlternatives(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val allProbs = repository.parseAllProbs(scan.allProbs)
        val top3 = allProbs.entries.sortedByDescending { it.value }.take(3)
        if (top3.isEmpty()) {
            binding.cardAlternatives.visibility = View.GONE
            return
        }

        binding.cardAlternatives.visibility = View.VISIBLE
        binding.alternativesLayout.removeAllViews()

        top3.forEachIndexed { index, entry ->
            val row = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { if (index > 0) topMargin = 12.dpToPx() }
            }

            val percent = (entry.value * 100).toInt()
            val name = displayName(entry.key, isRu) ?: entry.key
            val isMain = index == 0

            val nameRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val nameView = TextView(activity).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                text = name
                textSize = if (isMain) 14f else 13f
                setTextColor(activity.getColor(if (isMain) R.color.on_surface else R.color.on_surface_secondary))
                if (isMain) typeface = Typeface.DEFAULT_BOLD
            }
            val percentView = TextView(activity).apply {
                text = activity.getString(R.string.confidence_format, percent)
                textSize = if (isMain) 14f else 13f
                setTextColor(activity.getColor(if (isMain) R.color.primary else R.color.on_surface_secondary))
                if (isMain) typeface = Typeface.DEFAULT_BOLD
            }
            nameRow.addView(nameView)
            nameRow.addView(percentView)

            val progressBar = LinearProgressIndicator(activity).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 4.dpToPx() }
                max = 100
                progress = 0
                trackCornerRadius = 4.dpToPx()
                trackThickness = if (isMain) 8.dpToPx() else 6.dpToPx()
                setIndicatorColor(activity.getColor(if (isMain) R.color.primary else R.color.primary_light))
                trackColor = activity.getColor(R.color.surface_variant)
            }
            ValueAnimator.ofInt(0, percent).apply {
                duration = 600
                startDelay = (index * 150).toLong()
                interpolator = DecelerateInterpolator(2.5f)
                addUpdateListener { anim -> progressBar.progress = anim.animatedValue as Int }
            }.start()

            row.addView(nameRow)
            row.addView(progressBar)
            binding.alternativesLayout.addView(row)
        }
    }

    fun bindToxicity(scan: ScanEntity, repository: ScanRepository) {
        val card = binding.cardToxicity
        val title = binding.tvToxicityTitle
        val body = binding.tvToxicityBody
        val icon = binding.ivToxicityIcon

        val speciesList = repository.parseToxicSpeciesList(scan.toxicSpeciesList)
        when (scan.toxicitySource) {
            "db" -> if (scan.toxicToPets) {
                card.visibility = View.VISIBLE
                card.setCardBackgroundColor(activity.getColor(R.color.disease_red_bg))
                title.setTextColor(activity.getColor(R.color.disease_red))
                icon.setColorFilter(activity.getColor(R.color.disease_red))
                title.setText(R.string.toxicity_toxic_title)
                body.setTextColor(activity.getColor(R.color.disease_red))
                body.text = activity.getString(
                    R.string.toxicity_toxic_body,
                    speciesList.joinToString(", ") { localizeToxicSpecies(it) },
                )
            } else {
                card.visibility = View.VISIBLE
                card.setCardBackgroundColor(activity.getColor(R.color.healthy_green_bg))
                title.setTextColor(activity.getColor(R.color.healthy_green))
                icon.setColorFilter(activity.getColor(R.color.healthy_green))
                icon.setImageResource(R.drawable.ic_healthy)
                title.setText(R.string.toxicity_safe_title)
                body.setTextColor(activity.getColor(R.color.healthy_green))
                body.setText(R.string.toxicity_safe_body)
            }
            else -> {
                // Unknown source: the server couldn't pin a species. Showing a
                // big amber "pet safety unconfirmed" card on every scan reads as
                // a warning the user can't act on — hide it and let the user
                // pick a species via create-plant to trigger a real lookup.
                card.visibility = View.GONE
            }
        }
    }

    /** Localize the ASPCA toxic-species tokens (a closed set: cats / dogs /
     *  horses). Unknown tokens pass through unchanged. */
    private fun localizeToxicSpecies(token: String): String =
        when (token.trim().lowercase()) {
            "cats", "cat" -> activity.getString(R.string.toxicity_animal_cats)
            "dogs", "dog" -> activity.getString(R.string.toxicity_animal_dogs)
            "horses", "horse" -> activity.getString(R.string.toxicity_animal_horses)
            else -> token
        }

    fun setupTreatmentPrevention(
        scan: ScanEntity,
        repository: ScanRepository,
        isRu: Boolean,
        initialTab: Int = 0,
    ) {
        val treatment = repository.parseStringList(if (isRu) scan.treatmentRu else scan.treatment)
        val prevention = repository.parseStringList(if (isRu) scan.preventionRu else scan.prevention)

        if (treatment.isEmpty() && prevention.isEmpty()) {
            binding.cardTreatmentPrevention.visibility = View.GONE
            return
        }
        binding.cardTreatmentPrevention.visibility = View.VISIBLE

        if (treatment.isNotEmpty()) {
            binding.tabsTreatmentPrevention.addTab(
                binding.tabsTreatmentPrevention.newTab().setText(R.string.treatment_label).setTag("treatment"),
            )
            buildStepsList(binding.treatmentStepsLayout, treatment, R.drawable.bg_step_circle, R.color.primary)
        }
        if (prevention.isNotEmpty()) {
            binding.tabsTreatmentPrevention.addTab(
                binding.tabsTreatmentPrevention.newTab().setText(R.string.prevention_label).setTag("prevention"),
            )
            buildStepsList(binding.preventionStepsLayout, prevention, R.drawable.bg_step_circle_green, R.color.healthy_green)
        }

        // Show first tab content by default.
        if (treatment.isNotEmpty()) {
            binding.treatmentStepsLayout.visibility = View.VISIBLE
            binding.preventionStepsLayout.visibility = View.GONE
        } else {
            binding.treatmentStepsLayout.visibility = View.GONE
            binding.preventionStepsLayout.visibility = View.VISIBLE
        }

        binding.tabsTreatmentPrevention.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                when (tab.tag) {
                    "treatment" -> {
                        binding.treatmentStepsLayout.visibility = View.VISIBLE
                        binding.preventionStepsLayout.visibility = View.GONE
                    }
                    "prevention" -> {
                        binding.treatmentStepsLayout.visibility = View.GONE
                        binding.preventionStepsLayout.visibility = View.VISIBLE
                    }
                }
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })

        // Restore a previously-selected tab (rotation / process-death). Tab 0 is
        // already shown by default, so only re-select when a later tab was active.
        if (initialTab in 1 until binding.tabsTreatmentPrevention.tabCount) {
            binding.tabsTreatmentPrevention.getTabAt(initialTab)?.select()
        }

        // AI Plant Doctor follow-up — the same assistant as the Garden plant
        // detail. If this scan is already attached to a garden plant we open a
        // persistent plant session; otherwise an ephemeral scan session that
        // still carries the diagnosis context.
        binding.btnAskDoctor.setOnClickListener {
            Haptics.tick(activity)
            val app = activity.application as PlantDiseasesApp
            activity.lifecycleScope.launch {
                val plant = scan.plantId?.let { app.plantRepository.getPlant(it) }
                if (plant != null) {
                    com.plantdiseases.app.ui.garden.DoctorChatSheet.show(activity.supportFragmentManager, plant)
                } else {
                    com.plantdiseases.app.ui.garden.DoctorChatSheet.show(activity.supportFragmentManager, scan)
                }
            }
        }
    }

    /** Bundled offline example photo of the diagnosed problem, shown above the
     *  description. Server-hosted (moved out of the APK), disk-cached so it
     *  stays available offline after the first view. */
    fun loadDiseasePhotos(imageUrl: String) {
        val row = binding.llDiseasePhotosRow
        row.removeAllViews()
        val iv = ImageView(activity).apply {
            layoutParams = LinearLayout.LayoutParams(dpPx(150), dpPx(110))
                .apply { marginEnd = dpPx(8) }
            scaleType = ImageView.ScaleType.CENTER_CROP
        }
        Glide.with(activity)
            .load(imageUrl)
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .transform(CenterCrop(), RoundedCorners(24))
            .into(iv)
        row.addView(iv)
        binding.tvDiseasePhotosLabel.visibility = View.VISIBLE
        binding.hsvDiseasePhotos.visibility = View.VISIBLE
    }

    private fun buildStepsList(container: LinearLayout, steps: List<String>, bgRes: Int, colorRes: Int) {
        container.removeAllViews()
        val color = activity.getColor(colorRes)

        steps.forEachIndexed { index, step ->
            val row = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.TOP
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { if (index > 0) topMargin = 14.dpToPx() }
            }
            val numberView = TextView(activity).apply {
                // Fixed-size badge that never shrinks (weight stays on the text)
                // so the number can't crowd or overlap the step text.
                layoutParams = LinearLayout.LayoutParams(28.dpToPx(), 28.dpToPx())
                setBackgroundResource(bgRes)
                text = "${index + 1}"
                setTextColor(color)
                textSize = 13f
                gravity = Gravity.CENTER
                includeFontPadding = false
                typeface = Typeface.DEFAULT_BOLD
            }
            val textView = TextView(activity).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginStart = 12.dpToPx()
                    marginEnd = 4.dpToPx()
                    // Nudge the text down so its first line optically centers
                    // against the number badge instead of sitting above it.
                    topMargin = 3.dpToPx()
                }
                text = step
                setTextColor(activity.getColor(R.color.on_surface_secondary))
                textSize = 14f
                setLineSpacing(4f.dpToPxF(), 1f)
            }
            row.addView(numberView)
            row.addView(textView)
            container.addView(row)
        }
    }

    private fun dpPx(v: Int): Int = (v * activity.resources.displayMetrics.density).toInt()

    private fun Int.dpToPx(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        activity.resources.displayMetrics,
    ).toInt()

    private fun Float.dpToPxF(): Float = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this,
        activity.resources.displayMetrics,
    )
}
