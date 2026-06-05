package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.os.bundleOf
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityTopDisease
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import android.widget.Toast

/**
 * Community "second opinion" sheet. Opened from a low-confidence result
 * (ask the community) or from a "near you" carousel cell (answer someone
 * else). Loads the running consensus + existing opinions for [scanId], then
 * lets the user add their own (a known class + optional note).
 *
 * State-light and recreation-safe: the only inputs are stashed in arguments,
 * so a process-death restore reloads cleanly instead of crashing.
 */
class CommunityOpinionSheet : BottomSheetDialogFragment() {

    private var scanId: String = ""
    private var currentName: String? = null

    private var consensusView: TextView? = null
    private var listContainer: LinearLayout? = null
    private var emptyView: TextView? = null
    private var chipGroup: ChipGroup? = null
    private var noteInput: TextInputEditText? = null
    private var sendBtn: MaterialButton? = null

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.sheet_community_opinion, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        scanId = arguments?.getString(ARG_SCAN_ID).orEmpty()
        currentName = arguments?.getString(ARG_CURRENT_NAME)
        if (scanId.isBlank()) {
            dismissAllowingStateLoss()
            return
        }

        consensusView = view.findViewById(R.id.tv_opinion_consensus)
        listContainer = view.findViewById(R.id.ll_opinion_list)
        emptyView = view.findViewById(R.id.tv_opinion_empty)
        chipGroup = view.findViewById(R.id.chip_group_opinion)
        noteInput = view.findViewById(R.id.et_opinion_note)
        sendBtn = view.findViewById(R.id.btn_opinion_send)

        val currentCard = view.findViewById<View>(R.id.card_opinion_current)
        val currentView = view.findViewById<TextView>(R.id.tv_opinion_current)
        currentName?.takeIf { it.isNotBlank() }
            ?.let { currentView.text = it }
            ?: run { currentCard.visibility = View.GONE }

        val ctx = requireContext()
        CLASS_CATALOG.forEach { (key, labelRes) ->
            chipGroup?.addView(Chip(ctx).apply {
                id = View.generateViewId()
                text = getString(labelRes)
                isCheckable = true
                isCheckedIconVisible = true
                tag = key
            })
        }

        sendBtn?.isEnabled = false
        chipGroup?.setOnCheckedStateChangeListener { group, _ ->
            sendBtn?.isEnabled = group.checkedChipId != View.NO_ID
        }

        view.findViewById<MaterialButton>(R.id.btn_opinion_cancel)
            .setOnClickListener { dismiss() }
        sendBtn?.setOnClickListener { submit() }

        loadOpinions()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        consensusView = null
        listContainer = null
        emptyView = null
        chipGroup = null
        noteInput = null
        sendBtn = null
    }

    private fun loadOpinions() {
        val repo = (requireActivity().application as PlantDiseasesApp).scanRepository
        viewLifecycleOwner.lifecycleScope.launch {
            repo.getCommunityOpinions(scanId)
                .onSuccess { render(it.consensus, it.opinions) }
                .onFailure { render(emptyList(), emptyList()) }
        }
    }

    private fun submit() {
        val group = chipGroup ?: return
        val key = group.checkedChipId.takeIf { it != View.NO_ID }
            ?.let { group.findViewById<Chip>(it).tag as? String } ?: return
        val note = noteInput?.text?.toString()?.trim().orEmpty().ifBlank { null }
        val ctx = requireContext()
        Haptics.tick(ctx)
        sendBtn?.isEnabled = false

        val repo = (ctx.applicationContext as PlantDiseasesApp).scanRepository
        val request = CommunityOpinionRequest(
            scanId = scanId,
            userPseudoId = TelemetryIds.userPseudoId(ctx),
            suggestedDisease = key,
            note = note,
        )
        viewLifecycleOwner.lifecycleScope.launch {
            repo.submitCommunityOpinion(request)
                .onSuccess { resp ->
                    Toast.makeText(ctx, R.string.community_opinion_thanks, Toast.LENGTH_SHORT).show()
                    group.clearCheck()
                    noteInput?.setText("")
                    render(resp.consensus, resp.opinions)
                }
                .onFailure {
                    Toast.makeText(ctx, R.string.community_opinion_error, Toast.LENGTH_SHORT).show()
                    sendBtn?.isEnabled = true
                }
        }
    }

    private fun render(consensus: List<CommunityTopDisease>, opinions: List<CommunityOpinion>) {
        val consensusV = consensusView ?: return
        val list = listContainer ?: return
        val empty = emptyView ?: return

        if (consensus.isEmpty()) {
            consensusV.visibility = View.GONE
        } else {
            val parts = consensus.joinToString(", ") { "${labelFor(it.disease)} ×${it.count}" }
            consensusV.text = getString(R.string.community_opinion_consensus_prefix, parts)
            consensusV.visibility = View.VISIBLE
        }

        list.removeAllViews()
        if (opinions.isEmpty()) {
            empty.visibility = View.VISIBLE
        } else {
            empty.visibility = View.GONE
            opinions.forEach { list.addView(opinionRow(it)) }
        }
    }

    private fun opinionRow(op: CommunityOpinion): TextView {
        val ctx = requireContext()
        val label = labelFor(op.suggestedDisease)
        val text = if (op.note.isBlank()) "•  $label" else "•  $label — ${op.note}"
        return TextView(ctx).apply {
            this.text = text
            textSize = 14f
            setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
            val v = (6 * resources.displayMetrics.density).toInt()
            setPadding(0, v, 0, v)
        }
    }

    private fun labelFor(key: String): String =
        CLASS_CATALOG.firstOrNull { it.first == key }?.second
            ?.let { getString(it) } ?: key

    companion object {
        private const val TAG = "community-opinion-sheet"
        private const val ARG_SCAN_ID = "arg_scan_id"
        private const val ARG_CURRENT_NAME = "arg_current_name"

        /** Diagnosable classes a responder can pick. Keys must be present in
         *  the server's DISEASES_DATABASE or the POST is rejected (422).
         *  ``not_a_plant`` is included so a responder can flag that the
         *  shared photo isn't a plant at all — it's a valid class in both
         *  the classifier head and the server database. Labels reuse the
         *  feedback-correction strings. */
        private val CLASS_CATALOG: List<Pair<String, Int>> = listOf(
            "healthy" to R.string.feedback_class_healthy,
            "leaf_spot" to R.string.feedback_class_leaf_spot,
            "powdery_mildew" to R.string.feedback_class_powdery_mildew,
            "gray_mold_botrytis" to R.string.feedback_class_gray_mold_botrytis,
            "leaf_scorch_underwatering" to R.string.feedback_class_leaf_scorch_underwatering,
            "nutrient_deficiency" to R.string.feedback_class_nutrient_deficiency,
            "viral_disease" to R.string.feedback_class_viral_disease,
            "not_a_plant" to R.string.feedback_class_not_a_plant,
        )

        fun show(fm: FragmentManager, scanId: String, currentDisplayName: String?) {
            CommunityOpinionSheet().apply {
                arguments = bundleOf(
                    ARG_SCAN_ID to scanId,
                    ARG_CURRENT_NAME to currentDisplayName,
                )
            }.show(fm, TAG)
        }
    }
}
