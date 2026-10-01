package com.plantdiseases.app.ui.result

import android.content.Context
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.InAppReviewManager
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.PrivacyConsentPrefs
import kotlinx.coroutines.launch

/**
 * Owns the "was this diagnosis right?" card and the full feedback round-trip:
 * the yes/no choice, the correction sheet for a "no", submitting the
 * server-issued one-use feedback receipt, and the local "already sent" memory
 * so a scan can't be double-submitted.
 */
class FeedbackController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
    private val repository: ScanRepository,
    private val privacyConsentPrefs: PrivacyConsentPrefs,
    private val reviewManager: InAppReviewManager,
) {

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
            val loaded = currentScan()
            if (loaded != null) {
                submit(loaded, correct = false, correctedClass, reason)
            } else {
                // Sheet outlived the scan load (recreation) — re-fetch by id.
                activity.lifecycleScope.launch {
                    repository.getScanById(scanId)?.let {
                        submit(it, correct = false, correctedClass, reason)
                    }
                }
            }
        }
    }

    fun bind(scan: ScanEntity) {
        val card = binding.cardFeedback
        if (!privacyConsentPrefs.trainingEnabled) {
            card.visibility = View.GONE
            return
        }
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
     * Natural NAP / OOD / quality-abstention variant: a single "This is a
     * plant" action instead of confirming the raw diagnostic argmax. A
     * wrongly rejected plant photo is exactly the training signal the
     * rejection head needs (and gate / quality-policy evidence), and negative
     * feedback also uploads the image bytes (see [submit]).
     */
    fun bindNotAPlant(scan: ScanEntity) {
        val card = binding.cardFeedback
        if (!privacyConsentPrefs.trainingEnabled) {
            card.visibility = View.GONE
            return
        }
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

    /** Confirmation behind "This is a plant". Sends the dedicated out-of-head
     * correction label so retraining can prioritise these without free text. */
    private fun showIsPlantDialog(scan: ScanEntity) {
        com.google.android.material.dialog.MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.feedback_is_a_plant)
            .setMessage(R.string.feedback_is_a_plant_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.feedback_send) { _, _ ->
                submit(scan, correct = false, correctedClass = IS_A_PLANT_CLASS)
            }
            .show()
    }

    private fun showCorrectionSheet(scan: ScanEntity) {
        val isRu = LocaleHelper.isRussian(activity)
        val predictedKey = repository.verdictClassKey(scan)
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
    ) {
        // Visible progress, not just disabled buttons — a long send (slow
        // network, server cold start) previously looked like a freeze.
        binding.btnFeedbackYes.isEnabled = false
        binding.btnFeedbackNo.isEnabled = false
        binding.tvFeedbackStatus.visibility = View.VISIBLE
        binding.tvFeedbackStatus.setText(R.string.feedback_sending)
        activity.lifecycleScope.launch {
            val request = buildRequest(scan, correct, correctedClass, reason)
            if (request == null) {
                binding.feedbackChoiceRow.visibility = View.GONE
                binding.tvFeedbackStatus.visibility = View.VISIBLE
                binding.tvFeedbackStatus.setText(R.string.feedback_expired)
                Toast.makeText(activity, R.string.feedback_expired, Toast.LENGTH_SHORT).show()
                return@launch
            }
            val imageFile = java.io.File(scan.imagePath)
            val submission: Result<Unit> = if (!correct && imageFile.exists()) {
                repository.uploadFeedbackImage(request, imageFile)
            } else {
                repository.submitFeedback(request).map { }
            }
            submission.onSuccess {
                repository.consumeFeedbackReceipt(scan)
                markSubmitted(scan.id)
                binding.feedbackChoiceRow.visibility = View.GONE
                binding.tvFeedbackStatus.visibility = View.VISIBLE
                binding.tvFeedbackStatus.setText(R.string.feedback_sent)
                Haptics.success(activity)
                if (correct) {
                    // Confirming the diagnosis was right is the single
                    // strongest satisfaction signal on this screen — see
                    // InAppReviewManager for the shared cooldown.
                    reviewManager.maybeRequestReview()
                }
            }.onFailure { error ->
                if (error is ScanRepository.FeedbackReceiptException) {
                    repository.consumeFeedbackReceipt(scan)
                    binding.feedbackChoiceRow.visibility = View.GONE
                    binding.tvFeedbackStatus.visibility = View.VISIBLE
                    binding.tvFeedbackStatus.setText(R.string.feedback_expired)
                    return@onFailure
                }
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

    /** Build the strict, structured feedback payload authorized by the receipt. */
    private fun buildRequest(
        scan: ScanEntity,
        correct: Boolean,
        correctedClass: String?,
        reason: String?,
    ): FeedbackRequest? {
        if (!privacyConsentPrefs.trainingEnabled) return null
        val receipt = repository.feedbackReceipt(scan) ?: return null
        return FeedbackRequest(
            feedbackReceipt = receipt,
            correct = correct,
            correctedClass = correctedClass,
            reason = reason,
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
    }
}
