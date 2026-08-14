package com.plantdiseases.app.ui.result

import android.content.Context
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
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import android.widget.Toast
import javax.inject.Inject

/**
 * Community "second opinion" sheet. Opened from a low-confidence result
 * (ask the community) or from a "near you" carousel cell (answer someone
 * else). Loads the running consensus + existing opinions for [scanId], then
 * lets the user like the scan and add their own comment (a known class +
 * optional note) — more than one, if they come back with something to add.
 *
 * State-light and recreation-safe: the only inputs are stashed in arguments,
 * so a process-death restore reloads cleanly instead of crashing.
 */
@AndroidEntryPoint
class CommunityOpinionSheet : BottomSheetDialogFragment() {

    @Inject
    lateinit var scanRepository: ScanRepository

    private var scanId: String = ""
    private var currentName: String? = null

    // The model's verdict for this scan — the "Agree" target. Blank for legacy
    // callers, in which case Agree falls back to opening the picker.
    private var diseaseKey: String = ""

    private var ownerStatusRow: View? = null
    private var ownerStatusText: TextView? = null
    private var stopRequestBtn: MaterialButton? = null
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
    private var likeRow: View? = null
    private var likeIcon: ImageView? = null
    private var likeCountView: TextView? = null

    // Local mirror of the server's liked_by_me — updated optimistically on
    // tap and by every server response that actually carries it (loadOpinions
    // only; the opinion-submit response doesn't pass user_pseudo_id, so it
    // can't tell "liked" apart from "unknown" and must not overwrite this).
    private var liked = false
    private var likeInFlight = false

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
        // View-only mode: the scan's OWNER (showForOwner) reading the second
        // opinions on their own scan — no vote / picker / note, just the
        // consensus + replies. Every other caller now goes through show(),
        // which allows commenting.
        val viewOnly = arguments?.getBoolean(ARG_VIEW_ONLY, false) ?: false
        if (scanId.isBlank()) {
            dismissAllowingStateLoss()
            return
        }

        ownerStatusRow = view.findViewById(R.id.ll_opinion_owner_status)
        ownerStatusText = view.findViewById(R.id.tv_opinion_owner_status)
        stopRequestBtn = view.findViewById(R.id.btn_opinion_stop_request)
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
        likeRow = view.findViewById(R.id.ll_opinion_like)
        likeIcon = view.findViewById(R.id.iv_opinion_like)
        likeCountView = view.findViewById(R.id.tv_opinion_like_count)

        val currentCard = view.findViewById<View>(R.id.card_opinion_current)
        val currentView = view.findViewById<TextView>(R.id.tv_opinion_current)
        currentName?.takeIf { it.isNotBlank() }
            ?.let { currentView.text = it }
            ?: run { currentCard.visibility = View.GONE }
        renderMeta()
        renderPhoto(view)

        val ctx = requireContext()
        val cancelBtn = view.findViewById<MaterialButton>(R.id.btn_opinion_cancel)
        // Owning your own scan (showForOwner) is the only case that stays
        // permanently read-only — commenting on your own scan makes no sense.
        // Every other viewer, everywhere, may now leave more than one comment
        // (Community v2): there is no "already opined" lock any more.
        setupLike(ctx, isOwner = arguments?.getBoolean(ARG_OWNER, false) ?: false)

        if (viewOnly) {
            // The owner reading replies to their own scan: "here's what the
            // community thinks", no input controls.
            view.findViewById<TextView>(R.id.tv_opinion_title)
                .setText(R.string.community_opinion_replies_title)
            view.findViewById<TextView>(R.id.tv_opinion_subtitle)
                .setText(R.string.community_opinion_replies_subtitle)
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

    /** Model confidence line ("Confidence 87%"), hidden when unknown. */
    private fun renderMeta() {
        val meta = metaView ?: return
        val confidence = arguments?.getFloat(ARG_CONFIDENCE, -1f) ?: -1f
        if (confidence in 0f..1f) {
            meta.visibility = View.VISIBLE
            meta.text = getString(R.string.community_opinion_confidence, Math.round(confidence * 100))
        } else {
            meta.visibility = View.GONE
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
        ownerStatusRow = null
        ownerStatusText = null
        stopRequestBtn = null
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
        likeRow = null
        likeIcon = null
        likeCountView = null
    }

    private fun loadOpinions() {
        val ctx = requireContext()
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.getCommunityOpinions(scanId, TelemetryIds.userPseudoId(ctx))
                .onSuccess {
                    render(it.consensus, it.opinions)
                    renderOwnerStatus(it.wantsOpinion, it.opinionRequestedAt)
                    renderLike(it.likeCount, it.likedByMe)
                }
                .onFailure { render(emptyList(), emptyList()) }
        }
    }

    /**
     * Owner-only request controls: "Request open · N h left" + Stop. The
     * stop action is reachable at any point of the request's life —
     * including after replies arrived — and ends further notifications
     * (the collected replies stay visible). Changes are reported back to
     * the result screen via the Fragment Result API.
     */
    private fun renderOwnerStatus(wantsOpinion: Boolean, requestedAtSec: Long?) {
        val row = ownerStatusRow ?: return
        val isOwner = arguments?.getBoolean(ARG_OWNER, false) ?: false
        if (!isOwner || !wantsOpinion) {
            row.visibility = View.GONE
            return
        }
        row.visibility = View.VISIBLE
        val hours = requestedAtSec
            ?.let { (it + 72L * 3600L) * 1000L - System.currentTimeMillis() }
            ?.takeIf { it > 0L }
            ?.let { ((it + 3_599_999L) / 3_600_000L).toInt().coerceIn(1, 72) }
        ownerStatusText?.text = if (hours != null) {
            getString(R.string.second_opinion_sheet_status, hours)
        } else {
            getString(R.string.result_second_opinion_waiting)
        }
        stopRequestBtn?.setOnClickListener {
            stopRequestBtn?.isEnabled = false
            val ctx = requireContext()
            viewLifecycleOwner.lifecycleScope.launch {
                scanRepository
                    .cancelSecondOpinion(scanId, TelemetryIds.userPseudoId(ctx))
                    .onSuccess {
                        Toast.makeText(ctx, R.string.second_opinion_stopped, Toast.LENGTH_SHORT)
                            .show()
                        ownerStatusRow?.visibility = View.GONE
                        parentFragmentManager.setFragmentResult(
                            REQUEST_STATE_CHANGED,
                            Bundle.EMPTY,
                        )
                    }
                    .onFailure {
                        stopRequestBtn?.isEnabled = true
                        Toast.makeText(ctx, R.string.community_opinion_error, Toast.LENGTH_SHORT)
                            .show()
                    }
            }
        }
    }

    /** Wire the like toggle. Self-likes are refused server-side (an owner
     *  viewing their own scan, via showForOwner, always gets `liked: false`
     *  back) so no client-side owner check is needed beyond skipping the
     *  click listener — tapping it would just be a wasted round trip. */
    private fun setupLike(ctx: Context, isOwner: Boolean) {
        if (isOwner) return
        likeRow?.setOnClickListener {
            if (likeInFlight) return@setOnClickListener
            val previousCount = likeCountView?.text?.toString()?.toIntOrNull() ?: 0
            val previousLiked = liked
            val next = !liked
            likeInFlight = true
            Haptics.tick(ctx)
            renderLike((previousCount + if (next) 1 else -1).coerceAtLeast(0), next)
            viewLifecycleOwner.lifecycleScope.launch {
                scanRepository.setCommunityLike(scanId, TelemetryIds.userPseudoId(ctx), next)
                    .onSuccess { renderLike(it.count, it.liked) }
                    .onFailure { renderLike(previousCount, previousLiked) }
                likeInFlight = false
            }
        }
    }

    private fun renderLike(count: Int, likedByMe: Boolean) {
        liked = likedByMe
        likeCountView?.text = count.toString()
        likeIcon?.setImageResource(if (likedByMe) R.drawable.ic_heart_filled else R.drawable.ic_heart)
        likeIcon?.imageTintList = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(
                requireContext(),
                if (likedByMe) R.color.primary else R.color.on_surface_secondary,
            ),
        )
    }

    /** "Disagree" path: submit the disease the user picked from the chips. */
    private fun submitFromPicker() {
        val group = chipGroup ?: return
        val key = group.checkedChipId.takeIf { it != View.NO_ID }
            ?.let { group.findViewById<Chip>(it).tag as? String } ?: return
        val note = noteInput?.text?.toString()?.trim().orEmpty().ifBlank { null }
        submitDisease(key, note)
    }

    /** Post one comment (agree = the model's diagnosis, or a picked disease).
     *  Carries the author's nickname so the reply is attributed. Community v2:
     *  posting doesn't lock anything — the input resets so the same viewer can
     *  leave another comment (the consensus tally still counts one vote per
     *  author, see server/community.py opinions_for). */
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
                    resetInputAfterSubmit()
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

    /** After a successful post: clear the note + chip pick, hide the picker,
     *  bring the Agree/Disagree row back, and re-enable both vote buttons —
     *  ready for another comment. */
    private fun resetInputAfterSubmit() {
        val v = view ?: return
        noteInput?.text?.clear()
        chipGroup?.clearCheck()
        showPicker(v, visible = false)
        voteRow?.visibility = View.VISIBLE
        v.findViewById<MaterialButton>(R.id.btn_opinion_agree).isEnabled = true
        v.findViewById<MaterialButton>(R.id.btn_opinion_disagree).isEnabled = true
        sendBtn?.isEnabled = false
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
        private const val ARG_THUMB = "arg_thumb"
        private const val ARG_VIEW_ONLY = "arg_view_only"

        // True ONLY for the scan owner's view (showForOwner) — gates the
        // request-status row + "Stop request" action. Distinct from
        // ARG_VIEW_ONLY, which read-only viewers of OTHER people's scans
        // also set.
        private const val ARG_OWNER = "arg_owner"

        /** Fragment-result key fired when the owner changes the request's
         *  state from inside the sheet (stop) — the result screen listens
         *  and re-renders its second-opinion control. */
        const val REQUEST_STATE_CHANGED = "second_opinion_state_changed"

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
         *  model's diagnosis + confidence, then Agree / Disagree — or, for a
         *  scan this install already commented on, straight into the thread
         *  (any viewer may leave more than one comment). */
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
                    ARG_THUMB to item.thumbPath,
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
         *  opinions they requested — no class picker / note / send, but
         *  with the request-status row + Stop action. */
        fun showForOwner(fm: FragmentManager, scanId: String, currentDisplayName: String?) {
            CommunityOpinionSheet().apply {
                arguments = bundleOf(
                    ARG_SCAN_ID to scanId,
                    ARG_CURRENT_NAME to currentDisplayName,
                    ARG_VIEW_ONLY to true,
                    ARG_OWNER to true,
                )
            }.show(fm, TAG)
        }
    }
}
