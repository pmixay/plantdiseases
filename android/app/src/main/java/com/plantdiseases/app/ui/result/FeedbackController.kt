package com.plantdiseases.app.ui.result

import android.content.Context
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.TopProb
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Owns the "was this diagnosis right?" card and the full feedback round-trip:
 * the yes/no choice, the correction sheet for a "no", building the rich
 * [FeedbackRequest] payload, submitting it, and the local "already sent"
 * memory so a scan can't be double-submitted.
 */
class FeedbackController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
) : KoinComponent {
    private val repository: ScanRepository by inject()

    /**
     * Register the correction-sheet result listener in the activity's
     * onCreate — NOT when the sheet is shown — so the handoff survives the
     * activity + sheet being recreated together (rotation, process death with
     * the sheet open). A listener field on the sheet did not, and a recreated
     * sheet's Send button then silently dropped the user's feedback.
     */
    fun registerCorrectionListener(scanId: Long, currentScan: () -> ScanEntity?) {
        activity.supportFragmentManager.setFragmentResultListener(
            FeedbackCorrectionSheet.REQUEST_KEY,
            activity,
        ) { _, result ->
            val correctedClass = result.getString(FeedbackCorrectionSheet.RESULT_CORRECTED_CLASS)
            val reason = result.getString(FeedbackCorrectionSheet.RESULT_REASON)
            val note = result.getString(FeedbackCorrectionSheet.RESULT_NOTE)
            val loaded = currentScan()
            if (loaded != null) {
                submit(loaded, correct = false, correctedClass, reason, note)
            } else {
                // Sheet outlived the scan load (recreation) — re-fetch by id.
                activity.lifecycleScope.launch {
                    repository.getScanById(scanId)?.let {
                        submit(it, correct = false, correctedClass, reason, note)
                    }
                }
            }
        }
    }

    fun bind(scan: ScanEntity) {
        val card = binding.cardFeedback
        card.visibility = View.VISIBLE

        // Already-submitted feedback persists across re-opens of the same
        // scan — show the confirmation state and skip the buttons so the
        // user can't double-submit.
        if (isAlreadySubmitted(scan.id)) {
            binding.feedbackChoiceRow.visibility = View.GONE
            binding.tvFeedbackStatus.visibility = View.VISIBLE
            binding.tvFeedbackStatus.setText(R.string.feedback_sent)
            return
        }

        binding.tvFeedbackStatus.visibility = View.GONE
        binding.feedbackChoiceRow.visibility = View.VISIBLE
        binding.btnFeedbackYes.isEnabled = true
        binding.btnFeedbackNo.isEnabled = true

        binding.btnFeedbackYes.setOnClickListener {
            Haptics.tick(activity)
            submit(scan, correct = true)
        }
        binding.btnFeedbackNo.setOnClickListener {
            Haptics.tick(activity)
            showCorrectionSheet(scan)
        }
    }

    /**
     * NAP / blur-reject variant: a single "This is a plant" action (+ an
     * optional note) instead of the yes/no pair. A wrongly-rejected plant
     * photo is exactly the training signal the rejection head needs, and
     * negative feedback also uploads the image bytes (see [submit]).
     */
    fun bindNotAPlant(scan: ScanEntity) {
        val card = binding.cardFeedback
        card.visibility = View.VISIBLE

        if (isAlreadySubmitted(scan.id)) {
            binding.feedbackChoiceRow.visibility = View.GONE
            binding.tvFeedbackStatus.visibility = View.VISIBLE
            binding.tvFeedbackStatus.setText(R.string.feedback_sent)
            return
        }

        binding.tvFeedbackSubtitle.setText(R.string.feedback_nap_subtitle)
        binding.tvFeedbackStatus.visibility = View.GONE
        binding.feedbackChoiceRow.visibility = View.VISIBLE
        binding.btnFeedbackNo.visibility = View.GONE
        binding.btnFeedbackYes.isEnabled = true
        binding.btnFeedbackYes.setText(R.string.feedback_is_a_plant)
        binding.btnFeedbackYes.setOnClickListener {
            Haptics.tick(activity)
            showIsPlantDialog(scan)
        }
    }

    /** Optional-note dialog behind "This is a plant". Sends the dedicated
     *  out-of-head correction label so retraining can prioritise these. */
    private fun showIsPlantDialog(scan: ScanEntity) {
        val ctx = activity
        val input = com.google.android.material.textfield.TextInputEditText(ctx).apply {
            hint = ctx.getString(R.string.feedback_is_a_plant_note_hint)
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
            filters = arrayOf(android.text.InputFilter.LengthFilter(500))
            minLines = 2
        }
        val pad = (20 * ctx.resources.displayMetrics.density).toInt()
        val container = android.widget.FrameLayout(ctx).apply {
            setPadding(pad, pad / 2, pad, 0)
            addView(input)
        }
        com.google.android.material.dialog.MaterialAlertDialogBuilder(ctx)
            .setTitle(R.string.feedback_is_a_plant)
            .setMessage(R.string.feedback_is_a_plant_body)
            .setView(container)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.feedback_send) { _, _ ->
                val note = input.text?.toString()?.trim().orEmpty().ifBlank { null }
                submit(scan, correct = false, correctedClass = IS_A_PLANT_CLASS, note = note)
            }
            .show()
    }

    private fun showCorrectionSheet(scan: ScanEntity) {
        val isRu = LocaleHelper.isRussian(activity)
        val predictedKey = repository.topClassKey(scan)
        val predictedDisplay = predictedKey?.let { DiseaseLabels.displayName(it, isRu) }
            ?: (if (isRu) scan.diseaseNameRu else scan.diseaseName)
        // The submit lands via the Fragment Result API listener registered in
        // onCreate — survives recreation while this sheet is open.
        FeedbackCorrectionSheet.show(
            activity.supportFragmentManager,
            predictedDisplayName = predictedDisplay,
        )
    }

    private fun submit(
        scan: ScanEntity,
        correct: Boolean,
        correctedClass: String? = null,
        reason: String? = null,
        note: String? = null,
    ) {
        // Visible progress, not just disabled buttons — a long send (slow
        // network, server cold start) previously looked like a freeze.
        binding.btnFeedbackYes.isEnabled = false
        binding.btnFeedbackNo.isEnabled = false
        binding.tvFeedbackStatus.visibility = View.VISIBLE
        binding.tvFeedbackStatus.setText(R.string.feedback_sending)
        activity.lifecycleScope.launch {
            val request = buildRequest(scan, correct, correctedClass, reason, note)
            repository.submitFeedback(request).onSuccess {
                markSubmitted(scan.id)
                binding.feedbackChoiceRow.visibility = View.GONE
                binding.tvFeedbackStatus.visibility = View.VISIBLE
                binding.tvFeedbackStatus.setText(R.string.feedback_sent)
                Haptics.success(activity)
                // On negative feedback the original image is high-value for
                // retraining. Best-effort upload: failures are swallowed (the
                // JSON feedback row has already landed). The server filename is
                // the SHA-256 of the bytes, matching the image_hash in the JSON.
                if (!correct) {
                    val imageFile = java.io.File(scan.imagePath)
                    if (imageFile.exists()) {
                        runCatching { repository.uploadFeedbackImage(request.scanId, imageFile) }
                    }
                }
            }.onFailure { error ->
                binding.btnFeedbackYes.isEnabled = true
                binding.btnFeedbackNo.isEnabled = true
                binding.tvFeedbackStatus.visibility = View.GONE
                val msgRes = when (error) {
                    is ScanRepository.RateLimitException -> R.string.feedback_rate_limited
                    else -> R.string.feedback_failed
                }
                Toast.makeText(activity, msgRes, Toast.LENGTH_SHORT).show()
            }
        }
    }

    /**
     * Assemble the full feedback payload from a [ScanEntity] + the user's three
     * correction signals. All metadata is best-effort: when a field is unknown
     * we send null rather than fabricating a value, so server-side metrics
     * don't get polluted by defaults.
     */
    private suspend fun buildRequest(
        scan: ScanEntity,
        correct: Boolean,
        correctedClass: String?,
        reason: String?,
        note: String?,
    ): FeedbackRequest {
        // Prefer the server-issued displayedClassKey — the verdict the user
        // actually saw (post-weak-healthy-reroute). Fall back to
        // argmax(all_probs) only when the column is empty (legacy scans, or a
        // server old enough that the field wasn't returned); without this we'd
        // echo the pre-reroute argmax as predicted_class and rot the per-class
        // accuracy metrics server-side.
        val displayedKey = scan.displayedClassKey?.takeIf { it.isNotBlank() }
        val argmaxKey = repository.topClassKey(scan)
        val predictedKey = displayedKey ?: argmaxKey
        val allProbs = repository.parseAllProbs(scan.allProbs)
        val predictedConfidence = predictedKey?.let { allProbs[it] } ?: scan.confidence
        val top3 = allProbs.entries
            .asSequence()
            .sortedByDescending { it.value }
            .take(3)
            .map { TopProb(classKey = it.key, prob = it.value) }
            .toList()
            .takeIf { it.isNotEmpty() }
        val regionCount = repository.parseRegions(scan.regions).size
        val isRu = LocaleHelper.isRussian(activity)
        val latencyMs = (System.currentTimeMillis() - scan.timestamp).coerceAtLeast(0L)

        // client_scan_id is the random UUID minted at insert time. We never
        // send the local autoincrement primary key.
        val scanId = scan.clientScanId.takeIf { it.isNotBlank() }
            ?: java.util.UUID.randomUUID().toString()

        // SHA-256 of the original on-disk image so the corrections corpus can
        // be joined back to image bytes during retraining without uploading the
        // image itself. Off the UI thread; null when the file is gone (the
        // server treats it as optional).
        val imageHashHex = withContext(Dispatchers.IO) {
            ImageUtils.sha256Hex(java.io.File(scan.imagePath))
        }

        // The persisted prediction set (JSON array of class keys, stored at
        // saveScan time). Stale rows from before the local schema added it
        // won't have one — the server treats the field as optional.
        val predictionSet: List<String>? = scan.predictionSet?.let { raw ->
            runCatching {
                com.google.gson.Gson().fromJson(raw, Array<String>::class.java).toList()
            }.getOrNull()?.takeIf { it.isNotEmpty() }
        }

        return FeedbackRequest(
            scanId = scanId,
            correct = correct,
            correctedClass = correctedClass,
            reason = reason,
            note = note,
            predictedClass = predictedKey,
            predictedConfidence = predictedConfidence,
            top3 = top3,
            modelVersion = SERVER_MODEL_VERSION,
            appVersion = BuildConfig.VERSION_NAME,
            locale = if (isRu) "ru" else "en",
            platform = "android",
            userPseudoId = TelemetryIds.userPseudoId(activity),
            regionCount = regionCount,
            primaryRegionIndex = scan.primaryRegionIndex,
            feedbackLatencyMs = latencyMs,
            imageHash = imageHashHex,
            predictionSet = predictionSet,
        )
    }

    private fun isAlreadySubmitted(scanId: Long): Boolean =
        activity.getSharedPreferences(FEEDBACK_PREFS, Context.MODE_PRIVATE)
            .getBoolean("$FEEDBACK_KEY_PREFIX$scanId", false)

    private fun markSubmitted(scanId: Long) {
        activity.getSharedPreferences(FEEDBACK_PREFS, Context.MODE_PRIVATE).edit()
            .putBoolean("$FEEDBACK_KEY_PREFIX$scanId", true)
            .apply()
    }

    private companion object {
        private const val FEEDBACK_PREFS = "plantdiseases_feedback_prefs"
        private const val FEEDBACK_KEY_PREFIX = "scan_"

        // Out-of-head correction label for "the reject was wrong — there IS a
        // plant here". Accepted by the server's feedback allowlist.
        private const val IS_A_PLANT_CLASS = "is_a_plant"

        // Pinned to the classifier release in server/models/classes.json. When
        // the server exposes model_version in /api/analyze we'll trust that
        // value per-scan instead.
        private const val SERVER_MODEL_VERSION = "2.5.0"
    }
}
