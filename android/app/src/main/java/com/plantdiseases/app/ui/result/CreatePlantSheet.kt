package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.text.InputFilter
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.FragmentManager
import androidx.fragment.app.setFragmentResult
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.SpeciesCandidate
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.common.MeteredAccessUi
import com.plantdiseases.app.ui.garden.buildRoomPickerRow
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.SpeciesIdentity
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

/**
 * Bottom-sheet replacement for the v1 ``showCreatePlantForScanDialog``
 * alert. Hosts a polished form (name + species + PlantNet identify
 * + result chips) in a real Material outlined-box layout instead of
 * the cramped AlertDialog content view.
 *
 * The host activity receives the picked name, species and PlantNet
 * candidate (if any) via the Fragment Result API ([REQUEST_KEY]) — NOT a
 * listener field. A listener field set from the static show() does not
 * survive a configuration-change / process-death recreation (the system
 * rebuilds the fragment without calling show()), which silently dropped
 * the user's Save tap; the FragmentManager replays a pending result to
 * the re-registered host listener instead. All inputs ride in arguments
 * for the same reason. Networking + repository writes still belong on
 * the host so this fragment stays stateless across configuration changes.
 */
@AndroidEntryPoint
class CreatePlantSheet : BottomSheetDialogFragment() {

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var roomRepository: RoomRepository

    private var scanId: Long = -1
    private var initialName: String? = null
    private var prefilledScientific: String? = null
    private var prefilledCommon: String? = null
    private var prefilledScore: Float? = null
    private var selectedRoomId: Long? = null
    private var pickedScientific: String? = null
    private var pickedCommon: String? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.sheet_create_plant, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        scanId = arguments?.getLong(ARG_SCAN_ID, -1L) ?: -1L
        initialName = arguments?.getString(ARG_INITIAL_NAME)
        prefilledScientific = arguments?.getString(ARG_PREFILL_SCIENTIFIC)
        prefilledCommon = arguments?.getString(ARG_PREFILL_COMMON)
        prefilledScore = arguments?.takeIf { it.containsKey(ARG_PREFILL_SCORE) }
            ?.getFloat(ARG_PREFILL_SCORE)

        val tilName = view.findViewById<TextInputLayout>(R.id.til_name)
        val etName = view.findViewById<TextInputEditText>(R.id.et_name)
        val etSpecies = view.findViewById<TextInputEditText>(R.id.et_species)
        val identifyBtn = view.findViewById<MaterialButton>(R.id.btn_identify)
        val statusText = view.findViewById<TextView>(R.id.tv_suggest_status)
        val candidateChips = view.findViewById<LinearLayout>(R.id.candidate_chips)
        val cancelBtn = view.findViewById<MaterialButton>(R.id.btn_cancel)
        val saveBtn = view.findViewById<MaterialButton>(R.id.btn_save)

        val textLimitFilter = InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH)
        etName.filters = arrayOf(textLimitFilter)
        etSpecies.filters = arrayOf(textLimitFilter)
        etName.setText(savedInstanceState?.getString(STATE_DRAFT_NAME) ?: initialName.orEmpty())

        // Pre-fill from the species PlantNet already identified at
        // scan time. The user can still tap "Identify" to widen the
        // candidate set or override, but the common case (the
        // scan-time detection was correct) needs zero extra taps.
        // A saved null is meaningful: it means the user manually corrected
        // the visible species and deliberately cleared the scan identification.
        // Do not Elvis-fallback to the stale prefill after rotation.
        pickedScientific = if (savedInstanceState != null) {
            savedInstanceState.getString(STATE_PICKED_SCIENTIFIC)
        } else {
            prefilledScientific
        }
        pickedCommon = if (savedInstanceState != null) {
            savedInstanceState.getString(STATE_PICKED_COMMON)
        } else {
            prefilledCommon
        }
        selectedRoomId = savedInstanceState
            ?.takeIf { it.containsKey(STATE_ROOM_ID) }
            ?.getLong(STATE_ROOM_ID)
            ?.takeIf { it != RESULT_ROOM_ID_UNASSIGNED }
        val restoredSpeciesDraft = savedInstanceState?.getString(STATE_DRAFT_SPECIES)
        val canonicalDisplay = SpeciesIdentity.canonicalDisplay(pickedScientific, pickedCommon)
        (restoredSpeciesDraft ?: canonicalDisplay)
            ?.takeIf { it.isNotBlank() }
            ?.let { etSpecies.setText(it) }
        if (!canonicalDisplay.isNullOrBlank()) {
            statusText.text = getString(
                R.string.species_suggest_prefilled,
                ((prefilledScore ?: 0f) * 100).toInt().coerceIn(0, 100),
            )
            // The species is already filled in from the scan-time PlantNet
            // detection — a separate "Suggest species" button here is
            // redundant (it re-runs the same identify). It stays only for
            // scans where no species was detected, as the manual fallback.
            identifyBtn.visibility = View.GONE
        }
        var applyingCanonicalDisplay = false
        etSpecies.doAfterTextChanged { editable ->
            if (
                !applyingCanonicalDisplay &&
                SpeciesIdentity.manualEditClearsCanonical(
                    editable?.toString(),
                    pickedScientific,
                    pickedCommon,
                )
            ) {
                pickedScientific = null
                pickedCommon = null
                identifyBtn.visibility = View.VISIBLE
                statusText.text = ""
            }
        }

        identifyBtn.setOnClickListener {
            lifecycleScope.launch {
                val scan = scanRepository.getScanById(scanId)
                if (scan == null) {
                    statusText.text = getString(R.string.species_suggest_unavailable_body)
                    return@launch
                }
                val file = File(scan.imagePath)
                if (!file.exists()) {
                    statusText.text = getString(R.string.species_suggest_unavailable_body)
                    return@launch
                }
                identifyBtn.isEnabled = false
                statusText.text = getString(R.string.species_suggest_loading)
                candidateChips.removeAllViews()
                candidateChips.visibility = View.GONE
                val locale = if (LocaleHelper.isRussian(requireContext())) "ru" else "en"
                val res = scanRepository.identifySpecies(file, locale = locale)
                identifyBtn.isEnabled = true
                res.onSuccess { id ->
                    if (!id.available || id.results.isEmpty()) {
                        statusText.text = if (id.available) {
                            getString(R.string.species_suggest_no_results)
                        } else {
                            getString(plantNetFailureMessage(id.source))
                        }
                        return@onSuccess
                    }
                    statusText.text = getString(R.string.species_suggest_title)
                    id.results.take(3).forEach { candidate ->
                        candidateChips.addView(
                            buildCandidateRow(candidate) { picked ->
                                pickedScientific = picked.scientificName
                                pickedCommon = picked.displayCommonName
                                val display = picked.displayCommonName
                                    ?: picked.scientificName
                                    ?: getString(R.string.species_suggest_unknown_name)
                                applyingCanonicalDisplay = true
                                etSpecies.setText(display)
                                applyingCanonicalDisplay = false
                                identifyBtn.visibility = View.GONE
                                if (etName.text.isNullOrBlank() ||
                                    etName.text?.toString() == initialName
                                ) {
                                    etName.setText(display)
                                }
                            }
                        )
                    }
                    candidateChips.visibility = View.VISIBLE
                }.onFailure { error ->
                    statusText.text = if (MeteredAccessUi.handles(error)) {
                        MeteredAccessUi.showDialog(requireContext(), error)
                        getString(MeteredAccessUi.title(error))
                    } else {
                        getString(R.string.species_suggest_unavailable_body)
                    }
                }
            }
        }

        val roomPickerContainer = view.findViewById<LinearLayout>(R.id.room_picker_container)
        lifecycleScope.launch {
            val rooms = roomRepository.getRoomsForPicker()
            roomPickerContainer.addView(
                buildRoomPickerRow(requireActivity(), roomRepository, rooms, selectedRoomId) { picked -> selectedRoomId = picked },
            )
        }

        cancelBtn.setOnClickListener { dismiss() }
        saveBtn.setOnClickListener {
            val name = etName.text?.toString()?.trim().orEmpty()
            if (name.isEmpty()) {
                tilName.error = getString(R.string.plant_name_required)
                return@setOnClickListener
            }
            tilName.error = null
            setFragmentResult(
                REQUEST_KEY,
                Bundle().apply {
                    putLong(RESULT_SCAN_ID, scanId)
                    putString(RESULT_NAME, name)
                    putString(
                        RESULT_SPECIES,
                        etSpecies.text?.toString()?.trim().orEmpty().ifBlank { null },
                    )
                    putString(RESULT_SCIENTIFIC, pickedScientific)
                    putString(RESULT_COMMON, pickedCommon)
                    putLong(RESULT_ROOM_ID, selectedRoomId ?: RESULT_ROOM_ID_UNASSIGNED)
                },
            )
            dismiss()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        val sheetView = view
        outState.putString(
            STATE_DRAFT_NAME,
            sheetView?.findViewById<TextInputEditText>(R.id.et_name)?.text?.toString(),
        )
        outState.putString(
            STATE_DRAFT_SPECIES,
            sheetView?.findViewById<TextInputEditText>(R.id.et_species)?.text?.toString(),
        )
        outState.putString(STATE_PICKED_SCIENTIFIC, pickedScientific)
        outState.putString(STATE_PICKED_COMMON, pickedCommon)
        outState.putLong(STATE_ROOM_ID, selectedRoomId ?: RESULT_ROOM_ID_UNASSIGNED)
    }

    /**
     * Map the server's PlantNet ``source`` enum into a localized
     * failure message so the user sees a specific reason instead of
     * the generic "quota or upstream issue". Falls back to the
     * generic copy when the source is unknown.
     */
    private fun plantNetFailureMessage(source: String?): Int = when (source) {
        "no_api_key" -> R.string.species_suggest_unavailable_no_key
        "auth_failed" -> R.string.species_suggest_unavailable_auth_failed
        "rate_limited" -> R.string.species_suggest_unavailable_rate_limited
        "budget_exhausted" -> R.string.species_suggest_unavailable_budget_exhausted
        "budget_unavailable" -> R.string.species_suggest_unavailable_budget_unavailable
        else -> R.string.species_suggest_unavailable_body
    }

    private fun buildCandidateRow(
        candidate: SpeciesCandidate,
        onPick: (SpeciesCandidate) -> Unit,
    ): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = ctx.getDrawable(R.drawable.bg_species_chip)
            val pad = (12 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
            isClickable = true
            isFocusable = true
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = (6 * resources.displayMetrics.density).toInt()
            }
            setOnClickListener { onPick(candidate) }
        }
        val labels = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        labels.addView(
            TextView(ctx).apply {
                this.text = candidate.displayCommonName
                    ?: candidate.scientificName
                    ?: getString(R.string.species_suggest_unknown_name)
                textSize = 14f
                setTextColor(ctx.getColor(R.color.on_surface))
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL
                )
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            }
        )
        candidate.scientificName?.let { sci ->
            if (sci != (candidate.displayCommonName ?: "")) {
                labels.addView(
                    TextView(ctx).apply {
                        this.text = sci
                        textSize = 12f
                        setTextColor(ctx.getColor(R.color.on_surface))
                        setTypeface(typeface, android.graphics.Typeface.ITALIC)
                        maxLines = 1
                        ellipsize = android.text.TextUtils.TruncateAt.END
                    }
                )
            }
        }
        row.addView(labels)
        row.addView(
            TextView(ctx).apply {
                this.text = getString(
                    R.string.species_suggest_match,
                    (candidate.score * 100).toInt().coerceIn(0, 100),
                )
                textSize = 12f
                setTextColor(ctx.getColor(R.color.primary))
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL
                )
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply {
                    marginStart = (10 * resources.displayMetrics.density).toInt()
                }
            }
        )
        return row
    }

    companion object {
        private const val TEXT_FIELD_MAX_LENGTH = 1024
        private const val TAG = "create-plant-sheet"

        /** Fragment Result API contract with the host. */
        const val REQUEST_KEY = "create_plant_save"
        const val RESULT_SCAN_ID = "result_scan_id"
        const val RESULT_NAME = "result_name"
        const val RESULT_SPECIES = "result_species"
        const val RESULT_SCIENTIFIC = "result_scientific"
        const val RESULT_COMMON = "result_common"
        const val RESULT_ROOM_ID = "result_room_id"

        /** Bundle can't carry a nullable Long cleanly — same sentinel
         *  convention as [com.plantdiseases.app.ui.garden.RoomDetailActivity.UNASSIGNED_ID]. */
        const val RESULT_ROOM_ID_UNASSIGNED = -1L

        private const val ARG_SCAN_ID = "arg_scan_id"
        private const val ARG_INITIAL_NAME = "arg_initial_name"
        private const val ARG_PREFILL_SCIENTIFIC = "arg_prefill_scientific"
        private const val ARG_PREFILL_COMMON = "arg_prefill_common"
        private const val ARG_PREFILL_SCORE = "arg_prefill_score"
        private const val STATE_DRAFT_NAME = "state_draft_name"
        private const val STATE_DRAFT_SPECIES = "state_draft_species"
        private const val STATE_PICKED_SCIENTIFIC = "state_picked_scientific"
        private const val STATE_PICKED_COMMON = "state_picked_common"
        private const val STATE_ROOM_ID = "state_room_id"

        fun show(
            fm: FragmentManager,
            scan: ScanEntity,
            initialName: String?,
        ): CreatePlantSheet {
            val sheet = CreatePlantSheet()
            // Everything rides in arguments so a config-change / process-
            // death recreation rebuilds an identical sheet. The scan already
            // carries the PlantNet result from analyse time — pre-fill the
            // species fields so the user doesn't have to tap Identify a
            // second time. Each is nullable on ScanEntity; the empty case
            // falls back to the manual identify flow.
            sheet.arguments = Bundle().apply {
                putLong(ARG_SCAN_ID, scan.id)
                putString(ARG_INITIAL_NAME, initialName)
                putString(ARG_PREFILL_SCIENTIFIC, scan.speciesScientific?.takeIf { it.isNotBlank() })
                putString(ARG_PREFILL_COMMON, scan.speciesCommon?.takeIf { it.isNotBlank() })
                scan.speciesScore?.let { putFloat(ARG_PREFILL_SCORE, it) }
            }
            sheet.show(fm, TAG)
            return sheet
        }
    }
}
