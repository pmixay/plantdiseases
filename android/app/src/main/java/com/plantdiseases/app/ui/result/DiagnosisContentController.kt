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
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Renders the static diagnosis body: the pipeline-warning banner, the
 * alternative-diagnoses bars, the treatment/prevention tabs (with the AI plant
 * doctor follow-up) and the pet-toxicity card. Pure view binding off a
 * [ScanEntity] — no community or feedback state lives here.
 */
class DiagnosisContentController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
) : KoinComponent {
    private val plantRepository: PlantRepository by inject()

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

    /** Shared across both tabs so switching tab keeps the user's choice. */
    private var stepsExpanded = false

    /**
     * Cap the visible steps of the showing tab and reveal the toggle only when
     * that list is actually longer than the cap.
     */
    private fun applyStepsCollapse() {
        val list = if (binding.treatmentStepsLayout.visibility == View.VISIBLE) {
            binding.treatmentStepsLayout
        } else {
            binding.preventionStepsLayout
        }
        val total = list.childCount
        val cap = activity.resources.getInteger(R.integer.treatment_steps_collapsed_rows)
        if (total <= cap) {
            for (i in 0 until total) list.getChildAt(i)?.visibility = View.VISIBLE
            binding.stepsExpandRow.visibility = View.GONE
            return
        }
        for (i in cap until total) {
            list.getChildAt(i)?.visibility = if (stepsExpanded) View.VISIBLE else View.GONE
        }
        binding.stepsExpandRow.visibility = View.VISIBLE
        binding.tvStepsExpand.text = if (stepsExpanded) {
            activity.getString(R.string.list_show_fewer)
        } else {
            activity.getString(R.string.list_show_all, total)
        }
        binding.ivStepsChevron.setImageResource(
            if (stepsExpanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
        )
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
        applyStepsCollapse()
        binding.stepsExpandRow.setOnClickListener {
            stepsExpanded = !stepsExpanded
            applyStepsCollapse()
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
                // The cap applies to whichever list is now showing.
                applyStepsCollapse()
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
