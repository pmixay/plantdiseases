package com.plantdiseases.app.ui.result

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
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

            // Treatment
            val treatment = repository.parseStringList(
                if (isRu) scan.treatmentRu else scan.treatment
            )
            if (treatment.isNotEmpty()) {
                treatmentSection.visibility = View.VISIBLE
                tvTreatment.text = treatment.mapIndexed { i, t -> "${i + 1}. $t" }.joinToString("\n\n")
            } else {
                treatmentSection.visibility = View.GONE
            }

            // Prevention
            val prevention = repository.parseStringList(
                if (isRu) scan.preventionRu else scan.prevention
            )
            if (prevention.isNotEmpty()) {
                preventionSection.visibility = View.VISIBLE
                tvPrevention.text = prevention.mapIndexed { i, t -> "${i + 1}. $t" }.joinToString("\n\n")
            } else {
                preventionSection.visibility = View.GONE
            }

            // Date
            tvDate.text = ImageUtils.formatTimestamp(scan.timestamp)

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
                            // Delete image file from storage
                            try { File(scan.imagePath).delete() } catch (_: Exception) {}
                            appInstance.scanRepository.deleteScan(scan.id)
                            finish()
                        }
                    }
                    .setNegativeButton(R.string.cancel, null)
                    .show()
            }
        }
    }

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"
    }
}
