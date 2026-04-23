package com.plantdiseases.app.ui.analysis

import android.content.Context
import android.content.Intent
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
import com.plantdiseases.app.databinding.ActivityAnalysisBinding
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
            startAnalysis()
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
                            showError(getString(R.string.server_not_responding), getString(R.string.server_not_responding_detail))
                        is com.plantdiseases.app.data.repository.ScanRepository.NoNetworkException ->
                            showError(getString(R.string.no_network), getString(R.string.no_network_detail))
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

    private fun showError(title: String, detail: String) {
        binding.loadingLayout.visibility = View.GONE
        binding.errorLayout.visibility = View.VISIBLE
        binding.tvError.text = title
        binding.tvErrorDetail.text = detail
    }

    companion object {
        const val EXTRA_IMAGE_PATH = "extra_image_path"
        private const val TAG = "AnalysisActivity"
    }
}
