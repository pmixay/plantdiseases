package com.plantdiseases.app.ui.analysis

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityAnalysisBinding
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
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
        startAnalysis()
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

    private fun startAnalysis() {
        binding.loadingLayout.visibility = View.VISIBLE
        binding.errorLayout.visibility = View.GONE
        binding.progressBar.visibility = View.VISIBLE
        binding.progressBar.playAnimation()

        val app = application as PlantDiseasesApp

        lifecycleScope.launch {
            // Prepare image for upload
            val uploadFile = ImageUtils.prepareImageForUpload(this@AnalysisActivity, imagePath)

            // Send to server
            val result = app.scanRepository.analyzeImage(uploadFile)

            result.onSuccess { response ->
                // Save to local DB
                val scanId = app.scanRepository.saveScan(imagePath, response)

                // Navigate to result
                val intent = Intent(this@AnalysisActivity, ResultActivity::class.java).apply {
                    putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)
                }
                startActivity(intent)
                finish()

            }.onFailure { error ->
                binding.loadingLayout.visibility = View.GONE
                binding.errorLayout.visibility = View.VISIBLE
                binding.tvError.text = getString(R.string.analysis_error)
                binding.tvErrorDetail.text = error.localizedMessage ?: getString(R.string.unknown_error)
            }

            // Clean up temp upload file
            uploadFile.delete()
        }
    }

    companion object {
        const val EXTRA_IMAGE_PATH = "extra_image_path"
    }
}
