package com.plantdiseases.app.ui.analysis

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.bumptech.glide.signature.ObjectKey
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityAnalysisBinding
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.AquaClassifier
import com.plantdiseases.app.util.AquaInference
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.ModelFamilyPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class AnalysisActivity : AppCompatActivity() {

    private lateinit var binding: ActivityAnalysisBinding
    private var imagePath: String = ""
    // When set (≥0), the resulting scan is auto-attached to this plant after
    // save — used by the "New scan" affordance in PlantDetailActivity so the
    // photo lands in the plant's Photos/Timeline without a manual "Add to
    // garden" step. -1 ⇒ ordinary standalone scan.
    private var attachPlantId: Long = -1L
    // Held so onDestroy can dismiss the blurry-photo dialog — a config-change
    // recreate while it is showing would otherwise leak the dialog window.
    private var blurDialog: androidx.appcompat.app.AlertDialog? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityAnalysisBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Edge-to-edge (enforced on Android 15 / targetSdk 35): inset the
        // content region so the top preview clears the status bar and the
        // bottom Cancel button clears the gesture / nav bar instead of sitting
        // under it. Padding the root shifts both anchored edges inward.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(top = bars.top, bottom = bars.bottom, left = bars.left, right = bars.right)
            insets
        }

        imagePath = intent.getStringExtra(EXTRA_IMAGE_PATH) ?: run {
            finish()
            return
        }
        attachPlantId = intent.getLongExtra(EXTRA_PLANT_ID, -1L)

        setupUI()
        checkBlurAndAnalyze()
    }

    private fun setupUI() {
        // Show the captured image. Key the cache on the file's modified
        // time so a reused path (e.g. an overwritten crop) never serves a
        // stale bitmap.
        val imageFile = File(imagePath)
        Glide.with(this)
            .load(imageFile)
            .signature(ObjectKey(imageFile.lastModified()))
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
                // Photo is blurry — warn user. Keep the reference so onDestroy
                // can dismiss it and avoid a leaked window on recreation.
                blurDialog = MaterialAlertDialogBuilder(this@AnalysisActivity)
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
            // Prepared lazily, only on the server path, so a successful
            // on-device Aqua scan doesn't pay for a decode+resize+re-encode it
            // never sends. Nullable so the finally deletes only what we made.
            var uploadFile: File? = null
            try {
                val locale = if (LocaleHelper.isRussian(this@AnalysisActivity)) "ru" else "en"
                val family = ModelFamilyPrefs.selected(this@AnalysisActivity)

                // Aqua runs fully on-device when its TFLite model is installed —
                // no network, minimal result. The picker only offers Aqua once
                // the model is present, so a missing model simply falls through
                // to the server (tagged family=aqua).
                if (family == ModelFamily.AQUA && AquaClassifier.isAvailable(this@AnalysisActivity)) {
                    val response = runAquaOnDevice(imagePath)
                    if (response != null) {
                        proceedWithResult(imagePath, response)
                        return@launch
                    }
                    // Inference failed — fall through to the server.
                }

                // Server path: prepare the upload now. Decode + resize +
                // re-encode is CPU/IO heavy and allocates several bitmaps —
                // keep it off the main thread.
                uploadFile = try {
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

                val result = app.scanRepository.analyzeImage(
                    uploadFile, locale = locale, family = family.id,
                )

                result.onSuccess { response ->
                    proceedWithResult(imagePath, response)
                }.onFailure { error ->
                    when (error) {
                        is com.plantdiseases.app.data.repository.ScanRepository.ServerTimeoutException ->
                            showError(
                                getString(R.string.server_not_responding),
                                getString(R.string.server_not_responding_detail),
                            )
                        is com.plantdiseases.app.data.repository.ScanRepository.NoNetworkException -> {
                            // Offline — but if the on-device Aqua model is
                            // installed, answer with it instead of failing.
                            // (Had Aqua been the selected family we'd have run
                            // it above; reaching here means a server family was
                            // picked while offline, e.g. the default gamma.)
                            val offline = if (AquaClassifier.isAvailable(this@AnalysisActivity)) {
                                runAquaOnDevice(imagePath)
                            } else {
                                null
                            }
                            if (offline != null) {
                                proceedWithResult(imagePath, offline)
                            } else {
                                showError(
                                    getString(R.string.no_network),
                                    getString(R.string.no_network_detail),
                                )
                            }
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.RateLimitException ->
                            showError(getString(R.string.server_busy), getString(R.string.server_busy_detail))
                        is com.plantdiseases.app.data.repository.ScanRepository.ServerStartingException ->
                            showError(
                                getString(R.string.server_starting),
                                getString(R.string.server_starting_detail),
                            )
                        is com.plantdiseases.app.data.repository.ScanRepository.UnsupportedImageException ->
                            showError(
                                getString(R.string.unsupported_image_title),
                                getString(R.string.unsupported_image_detail),
                            )
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
                uploadFile?.let { f -> runCatching { f.delete() } }
            }
        }
    }

    /** Persist a result (from server OR on-device Aqua), fire the community
     *  upload, and navigate to the result screen. Shared by both paths. */
    private suspend fun proceedWithResult(
        imagePath: String,
        response: com.plantdiseases.app.data.model.AnalysisResponse,
    ) {
        val app = application as PlantDiseasesApp
        val scanId = app.scanRepository.saveScan(imagePath, response)

        // Auto-attach to the originating plant when launched from
        // PlantDetailActivity's "New scan" affordance, so the scan appears in
        // that plant's Photos + Timeline without a manual "Add to garden".
        if (attachPlantId >= 0L) {
            app.plantRepository.assignScanToPlant(scanId, attachPlantId)
        }

        getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
            .edit().putBoolean("new_scan_result", true).apply()

        // Fire-and-forget community upload — runs on the application-scoped
        // supervisor so it survives this activity finish()ing to the result
        // screen.
        val savedScan = app.scanRepository.getScanById(scanId)
        if (savedScan != null) {
            app.launchCommunityUpload(savedScan, response)
        }

        val intent = Intent(this@AnalysisActivity, ResultActivity::class.java).apply {
            putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)
        }
        startActivity(intent)
        finish()
    }

    /** Run the Aqua TFLite model on-device, off the main thread. Returns a
     *  minimal AnalysisResponse, or null if decode/inference failed (caller
     *  then falls back to the server). */
    private suspend fun runAquaOnDevice(imagePath: String): com.plantdiseases.app.data.model.AnalysisResponse? =
        withContext(Dispatchers.Default) {
            val classifier = AquaClassifier.create(this@AnalysisActivity) ?: return@withContext null
            try {
                val bitmap = AquaClassifier.decodeForInference(imagePath) ?: return@withContext null
                // Recycle in finally so a classify() failure can't leak the bitmap.
                val probs = try {
                    classifier.classify(bitmap)
                } finally {
                    bitmap.recycle()
                }
                AquaInference.buildResponse(probs)
            } catch (e: OutOfMemoryError) {
                // OutOfMemoryError is an Error, not an Exception — without this
                // clause a decode/inference OOM escapes and crashes the app
                // instead of falling back to the (OOM-guarded) server path.
                Log.w(TAG, "Aqua on-device OOM; falling back to server", e)
                null
            } catch (e: Exception) {
                Log.w(TAG, "Aqua on-device inference failed", e)
                null
            } finally {
                classifier.close()
            }
        }

    private fun showError(title: String, detail: String) {
        binding.loadingLayout.visibility = View.GONE
        binding.errorLayout.visibility = View.VISIBLE
        binding.tvError.text = title
        binding.tvErrorDetail.text = detail
    }

    override fun onDestroy() {
        blurDialog?.dismiss()
        blurDialog = null
        super.onDestroy()
    }

    companion object {
        const val EXTRA_IMAGE_PATH = "extra_image_path"
        /** Optional plant id; when present the saved scan is auto-attached to
         *  that plant (see PlantDetailActivity's "New scan" action). */
        const val EXTRA_PLANT_ID = "extra_plant_id"
        private const val TAG = "AnalysisActivity"
    }
}
