package com.plantdiseases.app.ui.result

import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.io.File

class ResultActivity : AppCompatActivity() {

    private lateinit var binding: ActivityResultBinding

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityResultBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val scanId = intent.getLongExtra(EXTRA_SCAN_ID, -1)
        if (scanId == -1L) {
            finish()
            return
        }

        binding.toolbar.setNavigationOnClickListener { finish() }
        loadResult(scanId)
    }

    private fun loadResult(scanId: Long) {
        val app = application as PlantDiseasesApp

        lifecycleScope.launch {
            val scan = app.scanRepository.getScanById(scanId)
            if (scan == null) {
                finish()
                return@launch
            }
            displayResult(scan, app.scanRepository)
        }
    }

    private fun displayResult(scan: ScanEntity, repository: ScanRepository) {
        val isRu = LocaleHelper.isRussian(this)

        binding.apply {
            // Image
            Glide.with(this@ResultActivity)
                .load(File(scan.imagePath))
                .transform(CenterCrop(), RoundedCorners(24))
                .into(ivPlant)

            // Heatmap overlay for diseased plants
            if (!scan.isHealthy) {
                heatmapOverlay.setDetectionRegion(0.5f, 0.45f, 0.35f)
                heatmapOverlay.postDelayed({
                    heatmapOverlay.animateIn()
                    tvHeatmapLabel.visibility = View.VISIBLE
                    tvHeatmapLabel.alpha = 0f
                    tvHeatmapLabel.animate().alpha(1f).setDuration(600).start()
                }, 500)
            }

            // Disease name
            tvDiseaseName.text = if (isRu) scan.diseaseNameRu else scan.diseaseName

            // Confidence
            val confidencePercent = (scan.confidence * 100).toInt()
            tvConfidence.text = getString(R.string.confidence_format, confidencePercent)
            progressConfidence.progress = confidencePercent

            // Status
            if (scan.isHealthy) {
                cardStatus.setCardBackgroundColor(getColor(R.color.healthy_green_bg))
                tvStatusLabel.text = getString(R.string.status_healthy)
                tvStatusLabel.setTextColor(getColor(R.color.healthy_green))
                ivStatusIcon.setImageResource(R.drawable.ic_healthy)
            } else {
                cardStatus.setCardBackgroundColor(getColor(R.color.disease_red_bg))
                tvStatusLabel.text = getString(R.string.status_disease)
                tvStatusLabel.setTextColor(getColor(R.color.disease_red))
                ivStatusIcon.setImageResource(R.drawable.ic_disease)
            }

            // Description
            tvDescription.text = if (isRu) scan.descriptionRu else scan.description

            // Treatment with step icons
            val treatment = repository.parseStringList(
                if (isRu) scan.treatmentRu else scan.treatment
            )
            if (treatment.isNotEmpty()) {
                treatmentSection.visibility = View.VISIBLE
                buildStepsList(treatmentStepsLayout, treatment, R.drawable.bg_step_circle, R.color.primary)
            } else {
                treatmentSection.visibility = View.GONE
            }

            // Prevention with step icons
            val prevention = repository.parseStringList(
                if (isRu) scan.preventionRu else scan.prevention
            )
            if (prevention.isNotEmpty()) {
                preventionSection.visibility = View.VISIBLE
                buildStepsList(preventionStepsLayout, prevention, R.drawable.bg_step_circle_green, R.color.healthy_green)
            } else {
                preventionSection.visibility = View.GONE
            }

            // Date
            tvDate.text = ImageUtils.formatTimestamp(scan.timestamp)

            // New Scan button
            btnNewScan.setOnClickListener {
                finish()
            }

            // Share button
            btnShare.setOnClickListener {
                val shareText = buildString {
                    append(if (isRu) "🌿 PlantDiseases — Диагноз\n\n" else "🌿 PlantDiseases — Diagnosis\n\n")
                    append(if (isRu) scan.diseaseNameRu else scan.diseaseName)
                    append(" (${(scan.confidence * 100).toInt()}%)\n\n")
                    append(if (isRu) scan.descriptionRu else scan.description)
                    val treatmentList = repository.parseStringList(if (isRu) scan.treatmentRu else scan.treatment)
                    if (treatmentList.isNotEmpty()) {
                        append("\n\n")
                        append(if (isRu) "💊 Лечение:\n" else "💊 Treatment:\n")
                        treatmentList.forEachIndexed { i, t -> append("${i + 1}. $t\n") }
                    }
                }
                val sendIntent = Intent().apply {
                    action = Intent.ACTION_SEND
                    putExtra(Intent.EXTRA_TEXT, shareText)
                    type = "text/plain"
                }
                startActivity(Intent.createChooser(sendIntent, getString(R.string.share)))
            }

            // Low confidence warning
            if (scan.confidence < 0.5f && !scan.isHealthy) {
                cardLowConfidence.visibility = View.VISIBLE
            } else {
                cardLowConfidence.visibility = View.GONE
            }

            // Delete button
            btnDelete.setOnClickListener {
                MaterialAlertDialogBuilder(this@ResultActivity)
                    .setTitle(R.string.delete_scan)
                    .setMessage(R.string.delete_scan_confirm)
                    .setPositiveButton(R.string.delete) { _, _ ->
                        val appInstance = application as PlantDiseasesApp
                        lifecycleScope.launch {
                            try { File(scan.imagePath).delete() } catch (_: Exception) {}
                            appInstance.scanRepository.deleteScan(scan.id)
                            finish()
                        }
                    }
                    .setNegativeButton(R.string.cancel, null)
                    .show()
            }

            // Animate cards from bottom
            animateCardsIn()
        }
    }

    private fun buildStepsList(
        container: LinearLayout,
        steps: List<String>,
        bgRes: Int,
        colorRes: Int
    ) {
        container.removeAllViews()
        val color = getColor(colorRes)

        steps.forEachIndexed { index, step ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.TOP
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply {
                    if (index > 0) topMargin = 12.dpToPx()
                }
            }

            // Step number circle
            val numberView = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(28.dpToPx(), 28.dpToPx())
                setBackgroundResource(bgRes)
                text = "${index + 1}"
                setTextColor(color)
                textSize = 13f
                gravity = Gravity.CENTER
                typeface = android.graphics.Typeface.DEFAULT_BOLD
            }

            // Step text
            val textView = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginStart = 12.dpToPx()
                }
                text = step
                setTextColor(getColor(R.color.on_surface_secondary))
                textSize = 14f
                setLineSpacing(4f.dpToPxF(), 1f)
            }

            row.addView(numberView)
            row.addView(textView)
            container.addView(row)
        }
    }

    private fun animateCardsIn() {
        val cards = listOf(
            binding.cardStatus,
            binding.cardConfidence,
            binding.cardDescription,
            binding.treatmentSection,
            binding.preventionSection,
            binding.btnNewScan
        ).filter { it.visibility == View.VISIBLE }

        cards.forEachIndexed { index, view ->
            view.translationY = 100f
            view.alpha = 0f
            view.animate()
                .translationY(0f)
                .alpha(1f)
                .setDuration(400)
                .setStartDelay((index * 80).toLong())
                .setInterpolator(DecelerateInterpolator())
                .start()
        }
    }

    private fun Int.dpToPx(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics
    ).toInt()

    private fun Float.dpToPxF(): Float = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this, resources.displayMetrics
    )

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"
    }
}
