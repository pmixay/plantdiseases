package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.SpeciesCandidate
import kotlinx.coroutines.launch
import java.io.File

/**
 * Bottom-sheet replacement for the v1 ``showCreatePlantForScanDialog``
 * alert. Hosts a polished form (name + species + PlantNet identify
 * + result chips) in a real Material outlined-box layout instead of
 * the cramped AlertDialog content view.
 *
 * The host activity receives the picked name, species and PlantNet
 * candidate (if any) via [Listener.onSave]. Networking + repository
 * writes still belong on the host so this fragment stays stateless
 * across configuration changes.
 */
class CreatePlantSheet : BottomSheetDialogFragment() {

    private var scanId: Long = -1
    private var initialName: String? = null
    private var prefilledScientific: String? = null
    private var prefilledCommon: String? = null
    private var prefilledScore: Float? = null
    private var listener: Listener? = null

    fun interface Listener {
        fun onSave(name: String, species: String?, scientific: String?, common: String?)
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.sheet_create_plant, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val tilName = view.findViewById<TextInputLayout>(R.id.til_name)
        val etName = view.findViewById<TextInputEditText>(R.id.et_name)
        val etSpecies = view.findViewById<TextInputEditText>(R.id.et_species)
        val identifyBtn = view.findViewById<MaterialButton>(R.id.btn_identify)
        val statusText = view.findViewById<TextView>(R.id.tv_suggest_status)
        val candidateChips = view.findViewById<LinearLayout>(R.id.candidate_chips)
        val cancelBtn = view.findViewById<MaterialButton>(R.id.btn_cancel)
        val saveBtn = view.findViewById<MaterialButton>(R.id.btn_save)

        etName.setText(initialName.orEmpty())

        // Pre-fill from the species PlantNet already identified at
        // scan time. The user can still tap "Identify" to widen the
        // candidate set or override, but the common case (the
        // scan-time detection was correct) needs zero extra taps.
        var pickedScientific: String? = prefilledScientific
        var pickedCommon: String? = prefilledCommon
        val prefilledDisplay = prefilledCommon ?: prefilledScientific
        if (!prefilledDisplay.isNullOrBlank()) {
            etSpecies.setText(prefilledDisplay)
            statusText.text = getString(
                R.string.species_suggest_prefilled,
                ((prefilledScore ?: 0f) * 100).toInt().coerceIn(0, 100),
            )
        }

        identifyBtn.setOnClickListener {
            val activity = activity ?: return@setOnClickListener
            val app = activity.application as PlantDiseasesApp
            lifecycleScope.launch {
                val scan = app.scanRepository.getScanById(scanId)
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
                val res = app.scanRepository.identifySpecies(file)
                identifyBtn.isEnabled = true
                res.onSuccess { id ->
                    if (!id.available || id.results.isEmpty()) {
                        statusText.text = if (id.available)
                            getString(R.string.species_suggest_no_results)
                        else
                            getString(plantNetFailureMessage(id.source))
                        return@onSuccess
                    }
                    statusText.text = getString(R.string.species_suggest_title)
                    id.results.take(3).forEach { candidate ->
                        candidateChips.addView(buildCandidateRow(candidate) { picked ->
                            pickedScientific = picked.scientificName
                            pickedCommon = picked.displayCommonName
                            val display = picked.displayCommonName
                                ?: picked.scientificName
                                ?: getString(R.string.species_suggest_unknown_name)
                            etSpecies.setText(display)
                            if (etName.text.isNullOrBlank() ||
                                etName.text?.toString() == initialName) {
                                etName.setText(display)
                            }
                        })
                    }
                    candidateChips.visibility = View.VISIBLE
                }.onFailure {
                    statusText.text = getString(R.string.species_suggest_unavailable_body)
                }
            }
        }

        cancelBtn.setOnClickListener { dismiss() }
        saveBtn.setOnClickListener {
            val name = etName.text?.toString()?.trim().orEmpty()
            if (name.isEmpty()) {
                tilName.error = getString(R.string.plant_name_required)
                return@setOnClickListener
            }
            tilName.error = null
            listener?.onSave(
                name,
                etSpecies.text?.toString()?.trim().orEmpty().ifBlank { null },
                pickedScientific,
                pickedCommon,
            )
            dismiss()
        }
    }

    /**
     * Map the server's PlantNet ``source`` enum into a localized
     * failure message so the user sees a specific reason instead of
     * the generic "quota or upstream issue". Falls back to the
     * generic copy when the source is unknown.
     */
    private fun plantNetFailureMessage(source: String?): Int = when (source) {
        "no_api_key"     -> R.string.species_suggest_unavailable_no_key
        "auth_failed"    -> R.string.species_suggest_unavailable_auth_failed
        "rate_limited"   -> R.string.species_suggest_unavailable_rate_limited
        else             -> R.string.species_suggest_unavailable_body
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
        labels.addView(TextView(ctx).apply {
            this.text = candidate.displayCommonName
                ?: candidate.scientificName
                ?: getString(R.string.species_suggest_unknown_name)
            textSize = 14f
            setTextColor(ctx.getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        candidate.scientificName?.let { sci ->
            if (sci != (candidate.displayCommonName ?: "")) {
                labels.addView(TextView(ctx).apply {
                    this.text = sci
                    textSize = 12f
                    setTextColor(ctx.getColor(R.color.on_surface_secondary))
                    setTypeface(typeface, android.graphics.Typeface.ITALIC)
                    maxLines = 1
                    ellipsize = android.text.TextUtils.TruncateAt.END
                })
            }
        }
        row.addView(labels)
        row.addView(TextView(ctx).apply {
            this.text = getString(
                R.string.species_suggest_match,
                (candidate.score * 100).toInt().coerceIn(0, 100),
            )
            textSize = 12f
            setTextColor(ctx.getColor(R.color.primary))
            typeface = android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                marginStart = (10 * resources.displayMetrics.density).toInt()
            }
        })
        return row
    }

    companion object {
        private const val TAG = "create-plant-sheet"

        fun show(
            fm: FragmentManager,
            scan: ScanEntity,
            initialName: String?,
            listener: Listener,
        ): CreatePlantSheet {
            val sheet = CreatePlantSheet()
            sheet.scanId = scan.id
            sheet.initialName = initialName
            // The scan already carries the PlantNet result from analyse
            // time — pre-fill the species fields so the user doesn't
            // have to tap Identify a second time. Each is nullable on
            // ScanEntity; the empty case falls back to the manual
            // identify flow.
            sheet.prefilledScientific = scan.speciesScientific?.takeIf { it.isNotBlank() }
            sheet.prefilledCommon = scan.speciesCommon?.takeIf { it.isNotBlank() }
            sheet.prefilledScore = scan.speciesScore
            sheet.listener = listener
            sheet.show(fm, TAG)
            return sheet
        }
    }
}
