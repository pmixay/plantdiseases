package com.plantdiseases.app.ui.analysis

import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.databinding.ActivityAnalysisBinding
import com.plantdiseases.app.ml.TfliteOfflineAnalyzer
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class AnalysisActivity : AppCompatActivity() {

    private lateinit var binding: ActivityAnalysisBinding
    private var imagePath: String = ""

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityAnalysisBinding.inflate(layoutInflater)
        setContentView(binding.root)

        imagePath = intent.getStringExtra(EXTRA_IMAGE_PATH) ?: run {
            finish()
            return
        }

        setupUI()
        checkBlurAndAnalyze()
    }

    private fun setupUI() {
        // Show the captured image
        Glide.with(this)
            .load(File(imagePath))
            .transform(CenterCrop(), RoundedCorners(32))
            .into(binding.ivPreview)

        binding.tvStatus.text = getString(R.string.analyzing)
        binding.tvStatusDetail.text = getString(R.string.analyzing_detail)

        binding.btnCancel.setOnClickListener {
            finish()
        }

        binding.btnRetry.setOnClickListener {
            binding.errorLayout.visibility = View.GONE
            binding.loadingLayout.visibility = View.VISIBLE
            binding.btnContinueAnyway.visibility = View.GONE
            startAnalysis()
        }

        binding.btnContinueAnyway.setOnClickListener {
            continueWithOfflineAnalysis()
        }
    }

    private fun checkBlurAndAnalyze() {
        lifecycleScope.launch {
            val blurScore = withContext(Dispatchers.IO) {
                ImageUtils.computeBlurScore(imagePath)
            }

            if (blurScore < 100.0) {
                // Photo is blurry — warn user
                MaterialAlertDialogBuilder(this@AnalysisActivity)
                    .setTitle(R.string.blurry_photo_title)
                    .setMessage(R.string.blurry_photo_message)
                    .setPositiveButton(R.string.continue_anyway) { _, _ ->
                        startAnalysis()
                    }
                    .setNegativeButton(R.string.cancel) { _, _ ->
                        finish()
                    }
                    .setCancelable(false)
                    .show()
            } else {
                startAnalysis()
            }
        }
    }

    private fun startAnalysis() {
        binding.loadingLayout.visibility = View.VISIBLE
        binding.errorLayout.visibility = View.GONE
        binding.progressBar.visibility = View.VISIBLE
        binding.progressBar.playAnimation()

        val app = application as PlantDiseasesApp

        lifecycleScope.launch {
            // Image preparation (decode + resize + re-encode) is CPU/IO heavy
            // and allocates several bitmaps — keep it off the main thread.
            val uploadFile = try {
                withContext(Dispatchers.IO) {
                    ImageUtils.prepareImageForUpload(this@AnalysisActivity, imagePath)
                }
            } catch (e: ImageUtils.ImageDecodeException) {
                Log.w(TAG, "Image decode failed", e)
                showError(getString(R.string.image_decode_error_title), getString(R.string.image_decode_error_detail))
                return@launch
            } catch (e: OutOfMemoryError) {
                Log.e(TAG, "Out of memory preparing upload", e)
                showError(getString(R.string.image_too_large_title), getString(R.string.image_too_large_detail))
                return@launch
            } catch (e: Exception) {
                Log.e(TAG, "Unexpected failure preparing upload", e)
                showError(getString(R.string.analysis_error), e.localizedMessage ?: getString(R.string.unknown_error))
                return@launch
            }

            try {
                val result = app.scanRepository.analyzeImage(uploadFile)

                result.onSuccess { response ->
                    val scanId = app.scanRepository.saveScan(imagePath, response)

                    getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
                        .edit().putBoolean("new_scan_result", true).apply()

                    val intent = Intent(this@AnalysisActivity, ResultActivity::class.java).apply {
                        putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)
                    }
                    startActivity(intent)
                    finish()
                }.onFailure { error ->
                    when (error) {
                        is com.plantdiseases.app.data.repository.ScanRepository.ServerTimeoutException ->
                            showError(
                                getString(R.string.server_not_responding),
                                getString(R.string.server_not_responding_detail),
                                allowOfflineFallback = true,
                            )
                        is com.plantdiseases.app.data.repository.ScanRepository.NoNetworkException ->
                            showError(
                                getString(R.string.no_network),
                                getString(R.string.no_network_detail),
                                allowOfflineFallback = true,
                            )
                        is com.plantdiseases.app.data.repository.ScanRepository.RateLimitException ->
                            showError(getString(R.string.server_busy), getString(R.string.server_busy_detail))
                        else -> {
                            Log.w(TAG, "Analysis failed", error)
                            showError(
                                getString(R.string.analysis_error),
                                error.localizedMessage ?: getString(R.string.unknown_error)
                            )
                        }
                    }
                }
            } finally {
                runCatching { uploadFile.delete() }
            }
        }
    }

    private fun continueWithOfflineAnalysis() {
        binding.errorLayout.visibility = View.GONE
        binding.loadingLayout.visibility = View.VISIBLE
        binding.progressBar.visibility = View.VISIBLE
        binding.tvStatus.text = getString(R.string.offline_analysis_title)
        binding.tvStatusDetail.text = getString(R.string.offline_analysis_detail)
        binding.progressBar.playAnimation()

        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            val response = withContext(Dispatchers.IO) {
                TfliteOfflineAnalyzer(this@AnalysisActivity).analyze(imagePath)
                    ?: buildOfflineAnalysisResponse(imagePath)
            }
            val scanId = app.scanRepository.saveScan(imagePath, response)

            getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
                .edit().putBoolean("new_scan_result", true).apply()

            startActivity(
                Intent(this@AnalysisActivity, ResultActivity::class.java).apply {
                    putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)
                }
            )
            finish()
        }
    }

    private fun buildOfflineAnalysisResponse(path: String): AnalysisResponse {
        val metrics = computeOfflinePlantMetrics(path)
        val diseased = metrics.brownRatio > 0.08f &&
            metrics.brownRatio > metrics.greenRatio * 0.22f &&
            metrics.contrast > 0.12f
        val confidence = if (diseased) {
            (0.55f + metrics.brownRatio * 1.8f + metrics.contrast * 0.25f).coerceIn(0.55f, 0.88f)
        } else {
            (0.58f + metrics.greenRatio * 0.65f - metrics.brownRatio * 0.35f).coerceIn(0.55f, 0.9f)
        }

        return if (diseased) {
            AnalysisResponse(
                diseaseName = "Possible visual stress",
                diseaseNameRu = "Возможный визуальный стресс",
                confidence = confidence,
                description = getString(R.string.offline_disease_description),
                descriptionRu = getString(R.string.offline_disease_description_ru),
                treatment = listOf(
                    getString(R.string.offline_disease_treatment_1),
                    getString(R.string.offline_disease_treatment_2),
                    getString(R.string.offline_disease_treatment_3),
                ),
                treatmentRu = listOf(
                    getString(R.string.offline_disease_treatment_1_ru),
                    getString(R.string.offline_disease_treatment_2_ru),
                    getString(R.string.offline_disease_treatment_3_ru),
                ),
                prevention = listOf(
                    getString(R.string.offline_prevention_1),
                    getString(R.string.offline_prevention_2),
                ),
                preventionRu = listOf(
                    getString(R.string.offline_prevention_1_ru),
                    getString(R.string.offline_prevention_2_ru),
                ),
                isHealthy = false,
                warnings = listOf("offline_color_heuristic"),
                pipelineMode = "offline_color_heuristic",
            )
        } else {
            AnalysisResponse(
                diseaseName = "Healthy Plant",
                diseaseNameRu = "Здоровое растение",
                confidence = confidence,
                description = getString(R.string.offline_healthy_description),
                descriptionRu = getString(R.string.offline_healthy_description_ru),
                treatment = emptyList(),
                treatmentRu = emptyList(),
                prevention = listOf(
                    getString(R.string.offline_prevention_1),
                    getString(R.string.offline_prevention_2),
                ),
                preventionRu = listOf(
                    getString(R.string.offline_prevention_1_ru),
                    getString(R.string.offline_prevention_2_ru),
                ),
                isHealthy = true,
                warnings = listOf("offline_color_heuristic"),
                pipelineMode = "offline_color_heuristic",
            )
        }
    }

    private fun computeOfflinePlantMetrics(path: String): OfflinePlantMetrics {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(path, bounds)
        var sample = 1
        while (bounds.outWidth / sample > 640 || bounds.outHeight / sample > 640) {
            sample *= 2
        }
        val bitmap = BitmapFactory.decodeFile(path, BitmapFactory.Options().apply {
            inSampleSize = sample
        }) ?: return OfflinePlantMetrics()

        var green = 0
        var brown = 0
        var considered = 0
        var luminanceSum = 0.0
        var luminanceSquaredSum = 0.0
        val hsv = FloatArray(3)
        val step = maxOf(1, minOf(bitmap.width, bitmap.height) / 180)

        for (y in 0 until bitmap.height step step) {
            for (x in 0 until bitmap.width step step) {
                val pixel = bitmap.getPixel(x, y)
                val r = Color.red(pixel)
                val g = Color.green(pixel)
                val b = Color.blue(pixel)
                Color.RGBToHSV(r, g, b, hsv)
                val hue = hsv[0]
                val saturation = hsv[1]
                val value = hsv[2]
                if (saturation < 0.12f || value < 0.08f || value > 0.96f) continue
                considered++
                if (hue in 70f..165f && saturation > 0.18f) green++
                if (hue in 12f..58f && saturation > 0.18f && value < 0.78f) brown++
                val luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                luminanceSum += luma
                luminanceSquaredSum += luma * luma
            }
        }
        bitmap.recycle()

        if (considered == 0) return OfflinePlantMetrics()
        val mean = luminanceSum / considered
        val variance = (luminanceSquaredSum / considered - mean * mean).coerceAtLeast(0.0)
        return OfflinePlantMetrics(
            greenRatio = green.toFloat() / considered,
            brownRatio = brown.toFloat() / considered,
            contrast = kotlin.math.sqrt(variance).toFloat(),
        )
    }

    private fun showError(title: String, detail: String, allowOfflineFallback: Boolean = false) {
        binding.loadingLayout.visibility = View.GONE
        binding.errorLayout.visibility = View.VISIBLE
        binding.btnContinueAnyway.visibility = if (allowOfflineFallback) View.VISIBLE else View.GONE
        binding.tvError.text = title
        binding.tvErrorDetail.text = detail
    }

    private data class OfflinePlantMetrics(
        val greenRatio: Float = 0f,
        val brownRatio: Float = 0f,
        val contrast: Float = 0f,
    )

    companion object {
        const val EXTRA_IMAGE_PATH = "extra_image_path"
        private const val TAG = "AnalysisActivity"
    }
}
