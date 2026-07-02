package com.plantdiseases.app.ui.result

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.os.bundleOf
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityTopDisease
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.Nicknames
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
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

    private val scanRepository: ScanRepository by inject()

    private var scanId: String = ""
    private var currentName: String? = null

    // The model's verdict for this scan — the "Agree" target. Blank for legacy
    // callers, in which case Agree falls back to opening the picker.
    private var diseaseKey: String = ""

    private var votesHeader: TextView? = null
    private var graphContainer: LinearLayout? = null
    private var listHeader: TextView? = null
    private var listContainer: LinearLayout? = null
    private var emptyView: TextView? = null
    private var metaView: TextView? = null
    private var voteRow: View? = null
    private var chipGroup: ChipGroup? = null
    private var noteInput: TextInputEditText? = null
    private var sendBtn: MaterialButton? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.sheet_community_opinion, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        scanId = arguments?.getString(ARG_SCAN_ID).orEmpty()
        currentName = arguments?.getString(ARG_CURRENT_NAME)
        diseaseKey = arguments?.getString(ARG_DISEASE_KEY).orEmpty()
        // View-only mode: the scan's OWNER is reading the second opinions
        // they asked for — no vote / picker / note, just the consensus + replies.
        val viewOnly = arguments?.getBoolean(ARG_VIEW_ONLY, false) ?: false
        if (scanId.isBlank()) {
            dismissAllowingStateLoss()
            return
        }

        votesHeader = view.findViewById(R.id.tv_opinion_votes_header)
        graphContainer = view.findViewById(R.id.ll_opinion_graph)
        listHeader = view.findViewById(R.id.tv_opinion_list_header)
        listContainer = view.findViewById(R.id.ll_opinion_list)
        emptyView = view.findViewById(R.id.tv_opinion_empty)
        metaView = view.findViewById(R.id.tv_opinion_meta)
        voteRow = view.findViewById(R.id.ll_opinion_vote)
        chipGroup = view.findViewById(R.id.chip_group_opinion)
        noteInput = view.findViewById(R.id.et_opinion_note)
        sendBtn = view.findViewById(R.id.btn_opinion_send)

        val currentCard = view.findViewById<View>(R.id.card_opinion_current)
        val currentView = view.findViewById<TextView>(R.id.tv_opinion_current)
        currentName?.takeIf { it.isNotBlank() }
            ?.let { currentView.text = it }
            ?: run { currentCard.visibility = View.GONE }
        renderMeta()
        renderPhoto(view)

        val ctx = requireContext()
        val cancelBtn = view.findViewById<MaterialButton>(R.id.btn_opinion_cancel)
        // One second opinion per scan: once this install has submitted, the
        // picker is locked and the sheet shows the replies read-only.
        val alreadyOpined = !viewOnly &&
            com.plantdiseases.app.util.OpinionPrefs.hasSubmittedOpinion(ctx, scanId)

        if (viewOnly || alreadyOpined) {
            // Strip the input controls. Owner = "here's what the community
            // thinks"; a responder who already used their one opinion sees a
            // "locked" note instead.
            if (viewOnly) {
                view.findViewById<TextView>(R.id.tv_opinion_title)
                    .setText(R.string.community_opinion_replies_title)
                view.findViewById<TextView>(R.id.tv_opinion_subtitle)
                    .setText(R.string.community_opinion_replies_subtitle)
            } else {
                view.findViewById<TextView>(R.id.tv_opinion_subtitle)
                    .setText(R.string.community_opinion_locked)
            }
            voteRow?.visibility = View.GONE
            view.findViewById<View>(R.id.tv_opinion_pick_label).visibility = View.GONE
            chipGroup?.visibility = View.GONE
            view.findViewById<View>(R.id.til_opinion_note).visibility = View.GONE
            sendBtn?.visibility = View.GONE
            emptyView?.setText(R.string.community_opinion_replies_empty)
            cancelBtn.setText(R.string.close)
            cancelBtn.setOnClickListener { dismiss() }
            loadOpinions()
            return
        }

        CLASS_CATALOG.forEach { (key, labelRes) ->
            chipGroup?.addView(
                Chip(ctx).apply {
                    id = View.generateViewId()
                    text = getString(labelRes)
                    isCheckable = true
                    isCheckedIconVisible = true
                    tag = key
                }
            )
        }

        // Vote-first (feedback-style): Agree confirms the model's diagnosis;
        // Disagree reveals the picker to choose the real disease. The picker +
        // note + send start hidden and appear on Disagree (or when there is no
        // known verdict to agree with).
        showPicker(view, visible = false)
        sendBtn?.isEnabled = false
        chipGroup?.setOnCheckedStateChangeListener { group, _ ->
            sendBtn?.isEnabled = group.checkedChipId != View.NO_ID
        }
        view.findViewById<MaterialButton>(R.id.btn_opinion_agree).setOnClickListener {
            if (diseaseKey.isNotBlank()) {
                submitDisease(diseaseKey)
            } else {
                voteRow?.visibility = View.GONE
                showPicker(view, visible = true)
            }
        }
        view.findViewById<MaterialButton>(R.id.btn_opinion_disagree).setOnClickListener {
            voteRow?.visibility = View.GONE
            showPicker(view, visible = true)
        }

        cancelBtn.setOnClickListener { dismiss() }
        sendBtn?.setOnClickListener { submitFromPicker() }

        loadOpinions()
    }

    /** Confidence + detected species line ("Confidence 87% · Monstera (92%)"),
     *  hidden when neither is known. */
    private fun renderMeta() {
        val meta = metaView ?: return
        val confidence = arguments?.getFloat(ARG_CONFIDENCE, -1f) ?: -1f
        val species = arguments?.getString(ARG_SPECIES)?.takeIf { it.isNotBlank() }
        val speciesScore = arguments?.getFloat(ARG_SPECIES_SCORE, -1f) ?: -1f
        val parts = mutableListOf<String>()
        if (confidence in 0f..1f) {
            parts += getString(R.string.community_opinion_confidence, Math.round(confidence * 100))
        }
        if (species != null) {
            parts += if (speciesScore in 0f..1f) {
                getString(R.string.community_opinion_species_fmt, species, Math.round(speciesScore * 100))
            } else {
                species
            }
        }
        if (parts.isEmpty()) {
            meta.visibility = View.GONE
        } else {
            meta.visibility = View.VISIBLE
            meta.text = parts.joinToString(" · ")
        }
    }

    /** Show the shared scan photo (borrowed from the scan-result screen) when
     *  the feed item carried a thumbnail; otherwise leave the slot hidden. */
    private fun renderPhoto(view: View) {
        val card = view.findViewById<View>(R.id.card_opinion_photo)
        val photo = view.findViewById<ImageView>(R.id.iv_opinion_photo)
        val thumb = arguments?.getString(ARG_THUMB)?.takeIf { it.isNotBlank() }
        if (thumb == null) {
            card.visibility = View.GONE
            return
        }
        card.visibility = View.VISIBLE
        Glide.with(this)
            .load("${ServerConfig.baseUrl}api/community/thumb/$thumb")
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .transform(CenterCrop(), RoundedCorners(24))
            .into(photo)
    }

    private fun showPicker(view: View, visible: Boolean) {
        val vis = if (visible) View.VISIBLE else View.GONE
        view.findViewById<View>(R.id.tv_opinion_pick_label).visibility = vis
        chipGroup?.visibility = vis
        view.findViewById<View>(R.id.til_opinion_note).visibility = vis
        sendBtn?.visibility = vis
    }

    override fun onDestroyView() {
        super.onDestroyView()
        votesHeader = null
        graphContainer = null
        listHeader = null
        listContainer = null
        emptyView = null
        metaView = null
        voteRow = null
        chipGroup = null
        noteInput = null
        sendBtn = null
    }

    private fun loadOpinions() {
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.getCommunityOpinions(scanId)
                .onSuccess { render(it.consensus, it.opinions) }
                .onFailure { render(emptyList(), emptyList()) }
        }
    }

    /** "Disagree" path: submit the disease the user picked from the chips. */
    private fun submitFromPicker() {
        val group = chipGroup ?: return
        val key = group.checkedChipId.takeIf { it != View.NO_ID }
            ?.let { group.findViewById<Chip>(it).tag as? String } ?: return
        val note = noteInput?.text?.toString()?.trim().orEmpty().ifBlank { null }
        submitDisease(key, note)
    }

    /** Post one opinion (agree = the model's diagnosis, or a picked disease) and
     *  lock the input. Carries the author's nickname so the reply is attributed. */
    private fun submitDisease(key: String, note: String? = null) {
        if (key.isBlank()) return
        val ctx = requireContext()
        Haptics.tick(ctx)
        sendBtn?.isEnabled = false
        view?.findViewById<MaterialButton>(R.id.btn_opinion_agree)?.isEnabled = false
        view?.findViewById<MaterialButton>(R.id.btn_opinion_disagree)?.isEnabled = false

        val request = CommunityOpinionRequest(
            scanId = scanId,
            userPseudoId = TelemetryIds.userPseudoId(ctx),
            suggestedDisease = key,
            note = note,
            nickname = Nicknames.nickname(ctx),
        )
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.submitCommunityOpinion(request)
                .onSuccess { resp ->
                    Toast.makeText(ctx, R.string.community_opinion_thanks, Toast.LENGTH_SHORT).show()
                    // One opinion per scan — record it and lock the input.
                    com.plantdiseases.app.util.OpinionPrefs.markSubmittedOpinion(ctx, scanId)
                    lockInputControls()
                    render(resp.consensus, resp.opinions)
                }
                .onFailure {
                    Toast.makeText(ctx, R.string.community_opinion_error, Toast.LENGTH_SHORT).show()
                    sendBtn?.isEnabled = true
                    view?.findViewById<MaterialButton>(R.id.btn_opinion_agree)?.isEnabled = true
                    view?.findViewById<MaterialButton>(R.id.btn_opinion_disagree)?.isEnabled = true
                }
        }
    }

    /** Hide the picker / note / send and show the "already opined" note —
     *  used once this install has left its single opinion on the scan. */
    private fun lockInputControls() {
        val v = view ?: return
        v.findViewById<TextView>(R.id.tv_opinion_subtitle)
            .setText(R.string.community_opinion_locked)
        voteRow?.visibility = View.GONE
        v.findViewById<View>(R.id.tv_opinion_pick_label).visibility = View.GONE
        chipGroup?.visibility = View.GONE
        v.findViewById<View>(R.id.til_opinion_note).visibility = View.GONE
        sendBtn?.visibility = View.GONE
    }

    private fun render(consensus: List<CommunityTopDisease>, opinions: List<CommunityOpinion>) {
        val header = votesHeader ?: return
        val graph = graphContainer ?: return
        val list = listContainer ?: return
        val empty = emptyView ?: return

        renderGraph(header, graph, consensus)

        list.removeAllViews()
        if (opinions.isEmpty()) {
            listHeader?.visibility = View.GONE
            empty.visibility = View.VISIBLE
        } else {
            empty.visibility = View.GONE
            listHeader?.text = getString(R.string.community_opinion_list_header, opinions.size)
            listHeader?.visibility = View.VISIBLE
            opinions.forEach { list.addView(opinionRow(it)) }
        }
    }

    /** Draw the vote graph: one horizontal percentage bar per disease,
     *  sorted high-to-low. The leading disease is tinted with the brand
     *  primary; the rest use a muted primary so the consensus reads at a
     *  glance. Hidden entirely until at least one vote exists. */
    private fun renderGraph(
        header: TextView,
        graph: LinearLayout,
        consensus: List<CommunityTopDisease>,
    ) {
        graph.removeAllViews()
        val total = consensus.sumOf { it.count }
        if (consensus.isEmpty() || total <= 0) {
            header.visibility = View.GONE
            graph.visibility = View.GONE
            return
        }
        header.visibility = View.VISIBLE
        graph.visibility = View.VISIBLE
        consensus
            .sortedByDescending { it.count }
            .forEachIndexed { index, entry ->
                graph.addView(voteBar(entry, total, leading = index == 0))
            }
    }

    private fun voteBar(entry: CommunityTopDisease, total: Int, leading: Boolean): View {
        val ctx = requireContext()
        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()
        val pct = Math.round(entry.count * 100f / total).coerceIn(0, 100)
        val fillColor = ContextCompat.getColor(
            ctx,
            if (leading) R.color.primary else R.color.primary_light,
        )
        val trackColor = ContextCompat.getColor(ctx, R.color.surface_variant)

        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(6), 0, dp(6))
        }

        // Label + percentage line.
        val labelLine = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        labelLine.addView(
            TextView(ctx).apply {
                text = labelFor(entry.disease)
                textSize = 13f
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
        )
        labelLine.addView(
            TextView(ctx).apply {
                text = getString(R.string.community_vote_percent, pct)
                textSize = 13f
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface_secondary))
            }
        )
        row.addView(labelLine)

        // Bar track with a weighted fill.
        val radius = dp(4).toFloat()
        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            background = android.graphics.drawable.GradientDrawable().apply {
                cornerRadius = radius
                setColor(trackColor)
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(8),
            ).apply { topMargin = dp(4) }
            weightSum = 100f
        }
        track.addView(
            View(ctx).apply {
                background = android.graphics.drawable.GradientDrawable().apply {
                    cornerRadius = radius
                    setColor(fillColor)
                }
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, pct.toFloat())
            }
        )
        if (pct < 100) {
            track.addView(
                View(ctx).apply {
                    layoutParams = LinearLayout.LayoutParams(
                        0,
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        (100 - pct).toFloat(),
                    )
                }
            )
        }
        row.addView(track)
        return row
    }

    /** One forum-style comment row: monogram avatar, nickname + relative
     *  time header, the suggested class (an opinion is always at least a
     *  vote), and the free-text note underneath when the author wrote one. */
    private fun opinionRow(op: CommunityOpinion): View {
        val ctx = requireContext()
        fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
        val nick = op.nickname?.takeIf { it.isNotBlank() }
        val who = nick ?: getString(R.string.community_anonymous_author)

        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, dp(8))
        }

        // Deterministic anonymous avatar, same recipe as the feed cards.
        row.addView(
            TextView(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(dp(32), dp(32)).apply {
                    marginEnd = dp(10)
                }
                gravity = android.view.Gravity.CENTER
                background = ContextCompat.getDrawable(ctx, R.drawable.bg_circle_surface_variant)
                backgroundTintList =
                    android.content.res.ColorStateList.valueOf(Avatars.colorFor(who))
                setTextColor(android.graphics.Color.WHITE)
                textSize = 11f
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL,
                )
                text = Avatars.monogramForNick(who)
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            },
        )

        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }

        // "Nickname · 2 hr. ago" header line.
        val head = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        head.addView(
            TextView(ctx).apply {
                text = who
                textSize = 13f
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL,
                )
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            },
        )
        if (op.createdAt > 0L) {
            head.addView(
                TextView(ctx).apply {
                    text = android.text.format.DateUtils.getRelativeTimeSpanString(
                        op.createdAt * 1000L,
                        System.currentTimeMillis(),
                        android.text.format.DateUtils.MINUTE_IN_MILLIS,
                        android.text.format.DateUtils.FORMAT_ABBREV_RELATIVE,
                    )
                    textSize = 12f
                    setTextColor(ContextCompat.getColor(ctx, R.color.on_surface_secondary))
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { marginStart = dp(8) }
                },
            )
        }
        col.addView(head)

        // The vote itself — every opinion carries at least a suggested class.
        col.addView(
            TextView(ctx).apply {
                text = labelFor(op.suggestedDisease)
                textSize = 14f
                setTextColor(ContextCompat.getColor(ctx, R.color.primary))
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL,
                )
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(2) }
            },
        )

        // Optional free-text comment.
        if (op.note.isNotBlank()) {
            col.addView(
                TextView(ctx).apply {
                    text = op.note
                    textSize = 14f
                    setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = dp(2) }
                },
            )
        }

        row.addView(col)
        return row
    }

    private fun labelFor(key: String): String =
        CLASS_CATALOG.firstOrNull { it.first == key }?.second
            ?.let { getString(it) } ?: key

    companion object {
        private const val TAG = "community-opinion-sheet"
        private const val ARG_SCAN_ID = "arg_scan_id"
        private const val ARG_CURRENT_NAME = "arg_current_name"
        private const val ARG_DISEASE_KEY = "arg_disease_key"
        private const val ARG_CONFIDENCE = "arg_confidence"
        private const val ARG_SPECIES = "arg_species"
        private const val ARG_SPECIES_SCORE = "arg_species_score"
        private const val ARG_THUMB = "arg_thumb"
        private const val ARG_VIEW_ONLY = "arg_view_only"

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

        /** Open the community scan detail for a feed/carousel item: shows the
         *  model's diagnosis + confidence + species, then Agree / Disagree. */
        fun show(
            fm: FragmentManager,
            item: com.plantdiseases.app.data.model.CommunityScanItem,
            currentDisplayName: String?,
        ) {
            CommunityOpinionSheet().apply {
                arguments = bundleOf(
                    ARG_SCAN_ID to item.scanId,
                    ARG_CURRENT_NAME to currentDisplayName,
                    ARG_DISEASE_KEY to item.disease,
                    ARG_CONFIDENCE to (item.confidence ?: -1f),
                    ARG_SPECIES to item.species,
                    ARG_SPECIES_SCORE to (item.speciesScore ?: -1f),
                    ARG_THUMB to item.thumbPath,
                )
            }.show(fm, TAG)
        }

        /** Read-only browse for the result-screen "similar scans" strip: shows
         *  the scan's photo + diagnosis context + community consensus/replies
         *  with NO way to leave an opinion. Browsing similar cases is decoupled
         *  from community participation (leaving opinions), which stays behind
         *  the opt-in on the dedicated "near you" screen. */
        fun showReadOnly(
            fm: FragmentManager,
            item: com.plantdiseases.app.data.model.CommunityScanItem,
            currentDisplayName: String?,
        ) {
            CommunityOpinionSheet().apply {
                arguments = bundleOf(
                    ARG_SCAN_ID to item.scanId,
                    ARG_CURRENT_NAME to currentDisplayName,
                    ARG_DISEASE_KEY to item.disease,
                    ARG_CONFIDENCE to (item.confidence ?: -1f),
                    ARG_SPECIES to item.species,
                    ARG_SPECIES_SCORE to (item.speciesScore ?: -1f),
                    ARG_THUMB to item.thumbPath,
                    ARG_VIEW_ONLY to true,
                )
            }.show(fm, TAG)
        }

        /** Minimal opener for callers that only have a scan_id + label (e.g.
         *  "my opinions"): no diagnosis context, so Agree falls back to the
         *  picker. */
        fun show(fm: FragmentManager, scanId: String, currentDisplayName: String?) {
            CommunityOpinionSheet().apply {
                arguments = bundleOf(
                    ARG_SCAN_ID to scanId,
                    ARG_CURRENT_NAME to currentDisplayName,
                )
            }.show(fm, TAG)
        }

        /** Read-only variant for the scan's OWNER to view the second
         *  opinions they requested — no class picker / note / send. */
        fun showForOwner(fm: FragmentManager, scanId: String, currentDisplayName: String?) {
            CommunityOpinionSheet().apply {
                arguments = bundleOf(
                    ARG_SCAN_ID to scanId,
                    ARG_CURRENT_NAME to currentDisplayName,
                    ARG_VIEW_ONLY to true,
                )
            }.show(fm, TAG)
        }
    }
}
