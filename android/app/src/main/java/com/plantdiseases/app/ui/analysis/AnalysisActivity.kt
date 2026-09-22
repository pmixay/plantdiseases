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
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.diagnosticContractViolation
import com.plantdiseases.app.data.model.isEffectivelyAbstained
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityAnalysisBinding
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.AquaClassifier
import com.plantdiseases.app.util.AquaInference
import com.plantdiseases.app.util.BlurGate
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.ModelFamilyPrefs
import com.plantdiseases.app.util.TelemetryReporter
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import javax.inject.Inject

@AndroidEntryPoint
class AnalysisActivity : AppCompatActivity() {

    private lateinit var binding: ActivityAnalysisBinding

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var careRepository: CareRepository
    private var imagePath: String = ""

    // When set (≥0), the resulting scan is auto-attached to this plant after
    // save — used by the "New scan" affordance in PlantDetailActivity so the
    // photo lands in the plant's Photos/Timeline without a manual "Add to
    // garden" step. -1 ⇒ ordinary standalone scan.
    private var attachPlantId: Long = -1L

    // "Improve online" upgrade of an offline scan: force a server family (skips
    // the on-device Aqua path and its offline fallback) and, on success, delete
    // the offline row it replaces. Null / -1 for an ordinary scan.
    private var forcedFamily: ModelFamily? = null
    private var replaceScanId: Long = -1L

    // Held so onDestroy can dismiss the blurry-photo dialog — a config-change
    // recreate while it is showing would otherwise leak the dialog window.
    private var blurDialog: androidx.appcompat.app.AlertDialog? = null
    private var blurQaFailed = false

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
        // "Improve online" upgrade: force a specific (server) family and replace
        // the offline scan being upgraded once the online result lands.
        forcedFamily = intent.getStringExtra(EXTRA_FORCE_FAMILY)?.let { ModelFamily.fromId(it) }
        replaceScanId = intent.getLongExtra(EXTRA_REPLACE_SCAN_ID, -1L)

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

            // Same statistic and floor as the server gate, so Aqua and gamma
            // reject the same photographs (util/BlurGate.kt).
            blurQaFailed = BlurGate.isTooBlurry(blurScore)
            if (blurQaFailed) {
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

        lifecycleScope.launch {
            // Prepared lazily, only on the server path, so a successful
            // on-device Aqua scan doesn't pay for a decode+resize+re-encode it
            // never sends. Nullable so the finally deletes only what we made.
            var uploadFile: File? = null
            try {
                val locale = if (LocaleHelper.isRussian(this@AnalysisActivity)) "ru" else "en"
                val family = forcedFamily ?: ModelFamilyPrefs.selected(this@AnalysisActivity)
                TelemetryReporter.crumb("analyze_start:${family.id}")

                // Aqua runs fully on-device when its TFLite model is installed —
                // no network, minimal result. The picker only offers Aqua once
                // the model is present, so a missing model simply falls through
                // to the server (tagged family=aqua).
                if (family == ModelFamily.AQUA && AquaClassifier.isAvailable(this@AnalysisActivity)) {
                    val response = runAquaOnDevice(
                        imagePath,
                        imageQualityRejected = blurQaFailed,
                    )
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
                    TelemetryReporter.report("image_prepare", "decode_fail", message = e.message)
                    showError(getString(R.string.image_decode_error_title), getString(R.string.image_decode_error_detail))
                    return@launch
                } catch (e: OutOfMemoryError) {
                    Log.e(TAG, "Out of memory preparing upload", e)
                    TelemetryReporter.report("image_prepare", "oom")
                    showError(getString(R.string.image_too_large_title), getString(R.string.image_too_large_detail))
                    return@launch
                } catch (e: Exception) {
                    Log.e(TAG, "Unexpected failure preparing upload", e)
                    TelemetryReporter.report("image_prepare", "error", message = e.message)
                    showError(getString(R.string.analysis_error), e.localizedMessage ?: getString(R.string.unknown_error))
                    return@launch
                }

                val result = scanRepository.analyzeImage(
                    uploadFile,
                    locale = locale,
                    family = family.id,
                )

                result.onSuccess { response ->
                    proceedWithResult(imagePath, response)
                }.onFailure { error ->
                    when (error) {
                        is com.plantdiseases.app.data.repository.ScanRepository.ServerTimeoutException -> {
                            TelemetryReporter.report("analyze", "timeout", family = family.id)
                            showError(
                                getString(R.string.server_not_responding),
                                getString(R.string.server_not_responding_detail),
                            )
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.NoNetworkException -> {
                            // Offline — but if the on-device Aqua model is
                            // installed, answer with it instead of failing.
                            // (Had Aqua been the selected family we'd have run
                            // it above; reaching here means a server family was
                            // picked while offline, e.g. the default gamma.)
                            // Don't silently fall back to Aqua when the user
                            // explicitly forced a server family (the "improve
                            // online" upgrade) — that would just reproduce the
                            // offline result they're trying to replace.
                            val offline = if (
                                forcedFamily == null &&
                                AquaClassifier.isAvailable(this@AnalysisActivity)
                            ) {
                                runAquaOnDevice(
                                    imagePath,
                                    imageQualityRejected = blurQaFailed,
                                )
                            } else {
                                null
                            }
                            if (offline != null) {
                                proceedWithResult(imagePath, offline)
                            } else {
                                // A real failure only when we couldn't recover
                                // offline (forced-server upgrade, or no Aqua model).
                                TelemetryReporter.report("analyze", "no_network", family = family.id)
                                showError(
                                    getString(R.string.no_network),
                                    getString(R.string.no_network_detail),
                                )
                            }
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.RateLimitException -> {
                            TelemetryReporter.report("analyze", "rate_limited", family = family.id)
                            showError(getString(R.string.server_busy), getString(R.string.server_busy_detail))
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.ServerStartingException -> {
                            TelemetryReporter.report("analyze", "server_starting", family = family.id)
                            showError(
                                getString(R.string.server_starting),
                                getString(R.string.server_starting_detail),
                            )
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.ModelUnavailableException -> {
                            TelemetryReporter.report(
                                "analyze",
                                "model_unavailable",
                                family = error.family,
                                message = error.reason,
                            )
                            showError(
                                getString(R.string.model_unavailable_title),
                                getString(R.string.model_unavailable_detail),
                            )
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.UnsupportedImageException -> {
                            TelemetryReporter.report("analyze", "unsupported_image", family = family.id)
                            showError(
                                getString(R.string.unsupported_image_title),
                                getString(R.string.unsupported_image_detail),
                            )
                        }
                        is com.plantdiseases.app.data.repository.ScanRepository.InvalidAnalysisResponseException -> {
                            TelemetryReporter.report(
                                "analyze",
                                "invalid_envelope",
                                family = family.id,
                                message = error.violation,
                            )
                            showError(
                                getString(R.string.analysis_contract_error_title),
                                getString(R.string.analysis_contract_error_detail),
                            )
                        }
                        else -> {
                            Log.w(TAG, "Analysis failed", error)
                            TelemetryReporter.report("analyze", "error", family = family.id, message = error.message ?: error.javaClass.simpleName)
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
        val violation = response.diagnosticContractViolation()
        if (violation != null) {
            TelemetryReporter.report(
                "analyze",
                "invalid_envelope",
                family = response.family,
                message = violation,
            )
            showError(
                getString(R.string.analysis_contract_error_title),
                getString(R.string.analysis_contract_error_detail),
            )
            return
        }
        val abstained = response.isEffectivelyAbstained()
        // Autosave the kept (cropped) photo to the user's device gallery only
        // now that we KNOW it's a plant — opt-in, camera captures only (moved
        // here from the camera screen precisely so abstained results never
        // land in the user's photo library). Reject verdicts still open the
        // full result screen (so the user can flag "this is a plant"), but
        // they're hidden from the in-app gallery and stats, and swept from
        // the DB after a couple of days.
        if (!abstained &&
            intent.getBooleanExtra(EXTRA_FROM_CAMERA, false) &&
            com.plantdiseases.app.util.AutosavePrefs.enabled(this)
        ) {
            val appContext = applicationContext
            val album = getString(R.string.autosave_album)
            val toSave = File(imagePath)
            lifecycleScope.launch(Dispatchers.IO) {
                ImageUtils.saveImageToGallery(appContext, toSave, album)
            }
        }

        // The Application is still needed here for launchCommunityUpload (an
        // app-lifecycle helper, not an injectable singleton).
        val app = application as PlantDiseasesApp
        val scanId = scanRepository.saveScan(imagePath, response)

        // Auto-attach to the originating plant when launched from
        // PlantDetailActivity's "New scan" affordance, so the scan appears in
        // that plant's Photos + Timeline without a manual "Add to garden".
        // Only server-diagnosed scans qualify — an offline Aqua scan (no
        // server-issued scan_id) is never added to the garden, even from the
        // in-garden scanner (which forces at least gamma anyway).
        if (!abstained && attachPlantId >= 0L && !response.scanId.isNullOrBlank()) {
            careRepository.attachScanWithStoredChecklist(
                scanId = scanId,
                existingPlantId = attachPlantId,
                storedChecklist = storedChecklist(response),
            )
        }

        // "Improve online" upgrade: carry over the replaced offline scan's plant
        // link (if any), then delete it so the upgraded online scan takes its
        // place instead of leaving a duplicate in history.
        if (!abstained && replaceScanId >= 0L && replaceScanId != scanId) {
            scanRepository.getScanById(replaceScanId)?.plantId?.let { pid ->
                careRepository.attachScanWithStoredChecklist(
                    scanId = scanId,
                    existingPlantId = pid,
                    storedChecklist = storedChecklist(response),
                )
            }
            scanRepository.deleteScan(replaceScanId)
        }

        // Fire-and-forget community upload — runs on the application-scoped
        // supervisor so it survives this activity finish()ing to the result
        // screen.
        val savedScan = scanRepository.getScanById(scanId)
        if (savedScan != null) {
            // MainActivity turns this flag into Gallery's "1" badge. Only set
            // it for a row Gallery can actually show: abstentions keep
            // a short-lived DB row for their result + feedback screen, but the
            // gallery intentionally filters that row out.
            if (!scanRepository.isAbstainedScan(savedScan)) {
                getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
                    .edit().putBoolean("new_scan_result", true).apply()
                app.launchCommunityUpload(savedScan, response)
            }
        }

        val intent = Intent(this@AnalysisActivity, ResultActivity::class.java).apply {
            putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)
            // This is the one path where a scan actually just happened, so
            // this is the one path that buzzes on arrival.
            putExtra(ResultActivity.EXTRA_FRESH_SCAN, true)
        }
        startActivity(intent)
        finish()
    }

    /** Local fallback for scans created directly from a plant detail. This
     * path never opens the Add-to-garden sheet, so attachment itself must
     * materialise the stored treatment checklist. */
    private fun storedChecklist(
        response: com.plantdiseases.app.data.model.AnalysisResponse,
    ): CareRepository.StoredChecklist? {
        if (response.isEffectivelyAbstained() || response.isHealthy) return null
        val preferred = if (LocaleHelper.isRussian(this)) response.treatmentRu else response.treatment
        val alternate = if (LocaleHelper.isRussian(this)) response.treatment else response.treatmentRu
        val treatment = preferred.filter { it.isNotBlank() }.ifEmpty { alternate.filter { it.isNotBlank() } }
        if (treatment.isEmpty()) return null
        val diseaseKey = response.classKey?.takeIf { it.isNotBlank() }
            ?: response.diseaseName.lowercase().replace(' ', '_')
        return CareRepository.StoredChecklist(
            carePlan = CarePlanResponse(
                disease = diseaseKey,
                keyAction = treatment.first(),
                treatment = treatment,
            ),
            diseaseKey = diseaseKey,
            diseaseLabelEn = response.diseaseName,
            diseaseLabelRu = response.diseaseNameRu,
        )
    }

    /** Run the Aqua TFLite model on-device, off the main thread. Returns a
     *  minimal AnalysisResponse, or null if decode/inference failed (caller
     *  then falls back to the server). */
    private suspend fun runAquaOnDevice(
        imagePath: String,
        imageQualityRejected: Boolean = false,
    ): com.plantdiseases.app.data.model.AnalysisResponse? =
        withContext(Dispatchers.Default) {
            // The user may acknowledge the blur dialog to inspect the neutral
            // result, but failed QA cannot enter the Aqua classifier. Aqua has
            // no calibrated OOD overlay to rescue an accepted argmax later.
            if (imageQualityRejected) {
                return@withContext AquaInference.buildImageQualityAbstention()
            }
            var classifier: AquaClassifier? = null
            try {
                // Interpreter construction and model mmap can themselves throw
                // OutOfMemoryError on low-RAM devices, so they belong inside
                // the same guarded fallback as decode and inference.
                val activeClassifier = AquaClassifier.create(this@AnalysisActivity)
                    ?: return@withContext null
                classifier = activeClassifier
                val bitmap = AquaClassifier.decodeForInference(imagePath) ?: return@withContext null
                // Recycle in finally so a classify() failure can't leak the bitmap.
                val probs = try {
                    activeClassifier.classify(bitmap)
                } finally {
                    bitmap.recycle()
                }
                val response = AquaInference.buildResponse(probs)
                val violation = response.diagnosticContractViolation()
                if (violation != null) {
                    Log.w(TAG, "Aqua envelope violated diagnostic contract: $violation")
                    TelemetryReporter.report(
                        "aqua_inference",
                        "invalid_envelope",
                        family = "aqua",
                        message = violation,
                    )
                    return@withContext null
                }
                response
            } catch (e: OutOfMemoryError) {
                // OutOfMemoryError is an Error, not an Exception — without this
                // clause a decode/inference OOM escapes and crashes the app
                // instead of falling back to the (OOM-guarded) server path.
                Log.w(TAG, "Aqua on-device OOM; falling back to server", e)
                TelemetryReporter.report("aqua_inference", "oom", family = "aqua")
                null
            } catch (e: Exception) {
                Log.w(TAG, "Aqua on-device inference failed", e)
                TelemetryReporter.report("aqua_inference", "error", family = "aqua", message = e.message)
                null
            } finally {
                classifier?.close()
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

        /** True when the photo came from the camera shutter (not a gallery
         *  import) — gates the opt-in device-gallery autosave, which runs
         *  only after an accepted plant verdict. */
        const val EXTRA_FROM_CAMERA = "extra_from_camera"

        /** Optional plant id; when present the saved scan is auto-attached to
         *  that plant (see PlantDetailActivity's "New scan" action). */
        const val EXTRA_PLANT_ID = "extra_plant_id"

        /** Force a specific model family (wire id), overriding the user's pick.
         *  Used by the offline-scan "improve online" upgrade to force a server
         *  family; also disables the offline-Aqua fallback for that run. */
        const val EXTRA_FORCE_FAMILY = "extra_force_family"

        /** Row id of an offline scan to delete once this (online) result saves —
         *  the "improve online" upgrade replaces the offline scan in place. */
        const val EXTRA_REPLACE_SCAN_ID = "extra_replace_scan_id"
        private const val TAG = "AnalysisActivity"
    }
}
