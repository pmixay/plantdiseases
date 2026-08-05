package com.plantdiseases.app.ui.result

import android.animation.ValueAnimator
import android.graphics.Typeface
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch

/**
 * Renders the static diagnosis body: the pipeline-warning banner, the
 * alternative-diagnoses bars and the AI plant doctor follow-up. Pure view
 * binding off a [ScanEntity] — no community or feedback state lives here.
 */
class DiagnosisContentController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
    private val plantRepository: PlantRepository,
) {

    private fun displayName(key: String, isRu: Boolean): String? =
        DiseaseLabels.displayName(key, isRu)

    fun bindPipelineWarnings(scan: ScanEntity, repository: ScanRepository) {
        // Confidence is part of the status card (and owns its Retry action).
        // The weak-healthy reroute is also already expressed by the displayed
        // disease verdict. Keep only pipeline messages that add distinct,
        // actionable context so the result does not become a warning stack.
        val warnings = repository.parseWarnings(scan.warnings).filterNot {
            it == "very_low_confidence" ||
                it == "low_confidence" ||
                it == "weak_healthy_rerouted"
        }
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

        // Priority order for non-confidence pipeline warnings: offline > blur
        // > not-a-leaf gate > uncertain healthy / disease suspected > viral
        // caveat.
        binding.cardPipelineWarning.visibility = View.VISIBLE
        when {
            // On-device (Aqua) scan — always caveat: the offline model has no
            // detector / post-processing pipeline behind it.
            "offline_aqua" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_offline_scan_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_offline_scan)
            }
            "blurry_image" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_blurry_image_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_blurry_image)
            }
            "nap_ood_gated" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_nap_ood_gated_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_nap_ood_gated)
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

    /** Wires the doctor action independently of the removed tabs card. */
    fun setupAskDoctor(scan: ScanEntity) {
        // AI Plant Doctor follow-up — the same assistant as the Garden plant
        // detail. If this scan is already attached to a garden plant we open a
        // persistent plant session; otherwise an ephemeral scan session that
        // still carries the diagnosis context.
        binding.btnAskDoctor.setOnClickListener {
            Haptics.tick(activity)
            activity.lifecycleScope.launch {
                val plant = scan.plantId?.let { plantRepository.getPlant(it) }
                if (plant != null) {
                    com.plantdiseases.app.ui.garden.DoctorChatSheet.show(activity.supportFragmentManager, plant)
                } else {
                    com.plantdiseases.app.ui.garden.DoctorChatSheet.show(activity.supportFragmentManager, scan)
                }
            }
        }
    }

    private fun Int.dpToPx(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        activity.resources.displayMetrics,
    ).toInt()
}
