package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.FragmentManager
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.R

/**
 * Structured correction sheet shown when the user taps "Wrong" on a
 * diagnosis. Collects three signals:
 *
 *   1. ``corrected_class`` — one of the server's known class keys,
 *      picked from a single-selection chip group. Maps 1:1 to the
 *      server's ``_FEEDBACK_ALLOWED_CORRECTIONS`` set; if the user
 *      can't find their answer they pick *Something else*, which
 *      lets them describe it in the free-text note instead.
 *   2. ``reason`` — one of the server's known reason keys, picked
 *      from a separate chip group.
 *   3. ``note`` — free-text up to 2000 chars (the server caps it
 *      identically). The TextInputLayout shows a live char counter.
 *
 * The sheet delivers the result via the Fragment Result API
 * ([REQUEST_KEY]); the host activity does the actual /api/feedback
 * request. The result-API handoff (rather than a listener field) is
 * load-bearing: a DialogFragment is recreated from its default
 * constructor on rotation / process death, and the old listener-field
 * version came back with a null listener — the Send button then
 * silently did nothing until the app was fully restarted.
 */
class FeedbackCorrectionSheet : BottomSheetDialogFragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.sheet_feedback_correction, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val ctx = requireContext()

        val classGroup = view.findViewById<ChipGroup>(R.id.chip_group_class)
        val reasonGroup = view.findViewById<ChipGroup>(R.id.chip_group_reason)
        val noteInput = view.findViewById<TextInputEditText>(R.id.et_note)
        val sendBtn = view.findViewById<MaterialButton>(R.id.btn_send)
        val cancelBtn = view.findViewById<MaterialButton>(R.id.btn_cancel)
        val predictedView = view.findViewById<android.widget.TextView>(R.id.tv_predicted_name)
        val predictedCard = view.findViewById<View>(R.id.card_predicted)

        // Arguments (not instance fields) so the value survives the
        // system recreating this fragment via its default constructor.
        arguments?.getString(ARG_PREDICTED_NAME)?.let { predictedView.text = it }
            ?: run { predictedCard.visibility = View.GONE }

        // Populate class chips from a fixed catalog so the keys we
        // send always match the server's known class set exactly.
        CLASS_CATALOG.forEach { (key, labelRes) ->
            classGroup.addView(
                Chip(ctx).apply {
                    id = View.generateViewId()
                    text = getString(labelRes)
                    isCheckable = true
                    isCheckedIconVisible = true
                    tag = key
                }
            )
        }

        REASON_CATALOG.forEach { (key, labelRes) ->
            reasonGroup.addView(
                Chip(ctx).apply {
                    id = View.generateViewId()
                    text = getString(labelRes)
                    isCheckable = true
                    isCheckedIconVisible = true
                    tag = key
                }
            )
        }

        cancelBtn.setOnClickListener { dismiss() }

        // Send is disabled until the user gives us at least one of
        // {corrected class, free-text note}. A reason chip alone
        // doesn't qualify — ``reason=wrong_class`` with no class +
        // no note was the most common empty-row shape in the
        // feedback corpus (rows 3, 4, 18) and carries no retraining
        // signal. The catalogues are seeded with chips that have
        // ``isCheckable=true``, so we listen for selection changes
        // on both groups + every keystroke in the note field.
        val updateSendState = {
            val classChecked = classGroup.checkedChipId != View.NO_ID
            val noteFilled = !noteInput.text?.toString()?.trim().isNullOrBlank()
            sendBtn.isEnabled = classChecked || noteFilled
        }
        sendBtn.isEnabled = false
        classGroup.setOnCheckedStateChangeListener { _, _ -> updateSendState() }
        reasonGroup.setOnCheckedStateChangeListener { _, _ -> updateSendState() }
        noteInput.addTextChangedListener(
            object : android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
                override fun afterTextChanged(s: android.text.Editable?) = updateSendState()
            },
        )

        sendBtn.setOnClickListener {
            // ``KEY_OTHER`` ("other") is a valid out-of-head
            // correction on the server — keep it, don't drop it.
            // The previous filter threw away the explicit
            // "Something else" signal users selected to tell us our
            // label set is incomplete.
            val correctedClass = classGroup.checkedChipId
                .takeIf { it != View.NO_ID }
                ?.let { classGroup.findViewById<Chip>(it).tag as? String }
            val reason = reasonGroup.checkedChipId
                .takeIf { it != View.NO_ID }
                ?.let { reasonGroup.findViewById<Chip>(it).tag as? String }
            val note = noteInput.text?.toString()?.trim().orEmpty().ifBlank { null }
            parentFragmentManager.setFragmentResult(
                REQUEST_KEY,
                Bundle().apply {
                    putString(RESULT_CORRECTED_CLASS, correctedClass)
                    putString(RESULT_REASON, reason)
                    putString(RESULT_NOTE, note)
                },
            )
            dismiss()
        }
    }

    companion object {
        private const val TAG = "feedback-correction-sheet"
        private const val KEY_OTHER = "other"
        private const val ARG_PREDICTED_NAME = "arg_predicted_name"

        /** Fragment Result API contract with the host activity. */
        const val REQUEST_KEY = "feedback_correction"
        const val RESULT_CORRECTED_CLASS = "corrected_class"
        const val RESULT_REASON = "reason"
        const val RESULT_NOTE = "note"

        /**
         * Class chip catalog. Keys must match the server's
         * ``_FEEDBACK_ALLOWED_CORRECTIONS`` set; if the server drops a
         * class, drop it from here too.
         */
        private val CLASS_CATALOG: List<Pair<String, Int>> = listOf(
            "healthy" to R.string.feedback_class_healthy,
            "powdery_mildew" to R.string.feedback_class_powdery_mildew,
            "gray_mold_botrytis" to R.string.feedback_class_gray_mold_botrytis,
            "leaf_spot" to R.string.feedback_class_leaf_spot,
            "viral_disease" to R.string.feedback_class_viral_disease,
            "pests" to R.string.feedback_class_pests,
            "not_a_plant" to R.string.feedback_class_not_a_plant,
            "edema" to R.string.feedback_class_edema,
            "sunburn_or_scorch" to R.string.feedback_class_sunburn_or_scorch,
            "leaf_scorch_underwatering" to R.string.feedback_class_leaf_scorch_underwatering,
            "nutrient_deficiency" to R.string.feedback_class_nutrient_deficiency,
            KEY_OTHER to R.string.feedback_class_other,
        )

        /** Reason chip catalog; keys must match the server enum. */
        private val REASON_CATALOG: List<Pair<String, Int>> = listOf(
            "wrong_class" to R.string.feedback_reason_wrong_class,
            "low_quality_image" to R.string.feedback_reason_low_quality_image,
            "occluded" to R.string.feedback_reason_occluded,
            "multiple_plants" to R.string.feedback_reason_multiple_plants,
            "ambiguous_symptoms" to R.string.feedback_reason_ambiguous_symptoms,
            "other" to R.string.feedback_reason_other,
        )

        fun show(
            fm: FragmentManager,
            predictedDisplayName: String?,
        ): FeedbackCorrectionSheet {
            val sheet = FeedbackCorrectionSheet()
            sheet.arguments = Bundle().apply {
                putString(ARG_PREDICTED_NAME, predictedDisplayName)
            }
            sheet.show(fm, TAG)
            return sheet
        }
    }
}
