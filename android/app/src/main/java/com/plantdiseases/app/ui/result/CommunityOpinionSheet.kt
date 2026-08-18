package com.plantdiseases.app.ui.result

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.core.os.bundleOf
import androidx.fragment.app.FragmentManager
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityTopDisease
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.common.SpeciesInfoCardView
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ServerConfig
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
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

    @Inject
    lateinit var authRepository: AuthRepository

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
    private var currentVoteRow: View? = null
    private var currentVoteText: TextView? = null
    private var changeVoteBtn: MaterialButton? = null
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

    // The caller's own current pick, per the server's my_opinion (opinions_for
    // dedupes per author, so a second vote replaces this rather than adding to
    // it) — null means "haven't voted", in which case the sheet still shows
    // Agree/Disagree instead of Change opinion.
    private var myOpinion: String? = null

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
        currentVoteRow = view.findViewById(R.id.ll_opinion_current_vote)
        currentVoteText = view.findViewById(R.id.tv_opinion_current_vote)
        changeVoteBtn = view.findViewById(R.id.btn_opinion_change)
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

        val isOwner = arguments?.getBoolean(ARG_OWNER, false) ?: false
        val safetyRow = view.findViewById<View>(R.id.ll_community_scan_safety)
        if (isOwner) {
            safetyRow.visibility = View.GONE
        } else {
            view.findViewById<MaterialButton>(R.id.btn_community_report_scan)
                .setOnClickListener { showReportDialog(TARGET_SCAN, scanId) }
            view.findViewById<MaterialButton>(R.id.btn_community_block_scan_author)
                .setOnClickListener { confirmBlock(TARGET_SCAN, scanId) }
        }

        val ctx = requireContext()
        val cancelBtn = view.findViewById<MaterialButton>(R.id.btn_opinion_cancel)
        if (viewOnly) {
            // Strip every mutation control while leaving public consensus and
            // replies visible to guests and signed-in readers alike.
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

        // Agree and Disagree both open the same picker + note — Agree just
        // pre-selects the model's own class, so agreeing silently (no note)
        // is still one tap, but everyone gets a chance to actually write
        // something before sending. An instant-submit Agree was the reason
        // "leave a comment" read as "click Agree or Disagree, nothing else":
        // there was never a text box in that path.
        showPicker(view, visible = false)
        sendBtn?.isEnabled = false
        chipGroup?.setOnCheckedStateChangeListener { group, _ ->
            sendBtn?.isEnabled = group.checkedChipId != View.NO_ID
        }
        view.findViewById<MaterialButton>(R.id.btn_opinion_agree).setOnClickListener {
            voteRow?.visibility = View.GONE
            showPicker(view, visible = true)
            if (diseaseKey.isNotBlank()) preselectChip(diseaseKey)
        }
        view.findViewById<MaterialButton>(R.id.btn_opinion_disagree).setOnClickListener {
            voteRow?.visibility = View.GONE
            showPicker(view, visible = true)
        }
        changeVoteBtn?.setOnClickListener {
            currentVoteRow?.visibility = View.GONE
            showPicker(view, visible = true)
            myOpinion?.takeIf { it.isNotBlank() }?.let { preselectChip(it) }
        }

        cancelBtn.setOnClickListener { dismiss() }
        sendBtn?.setOnClickListener { submitFromPicker() }

        expandSheet(view)
        loadOpinions()
    }

    /**
     * Open expanded, not collapsed. The scan photo sits at the top and is
     * the tallest element here, so at the default collapsed peek height the
     * Agree/Disagree row — and the picker + note + Send below it — landed
     * off-screen: the sheet looked like a photo viewer with no way to
     * comment. Same treatment DoctorChatSheet / ReminderScheduleSheet /
     * GuideDetailSheet already use.
     */
    private fun expandSheet(view: View) {
        val sheet = view.parent as? View ?: return
        val behavior = BottomSheetBehavior.from(sheet)
        val maxHeight = (resources.displayMetrics.heightPixels * 0.9f).toInt()
        sheet.layoutParams = sheet.layoutParams.apply { height = maxHeight }
        behavior.maxHeight = maxHeight
        behavior.peekHeight = maxHeight
        behavior.skipCollapsed = true
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
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
            .load("${ServerConfig.appBaseUrl}api/community/thumb/$thumb")
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .transform(CenterCrop(), RoundedCorners(24))
            .into(photo)
    }

    /** Check the chip matching [key] (the model's own verdict, for the Agree
     *  shortcut) and enable Send immediately — the user doesn't have to
     *  re-tap a chip they already implicitly picked by hitting Agree. */
    private fun preselectChip(key: String) {
        val group = chipGroup ?: return
        (0 until group.childCount)
            .map { group.getChildAt(it) as Chip }
            .firstOrNull { it.tag == key }
            ?.let { group.check(it.id) }
        sendBtn?.isEnabled = group.checkedChipId != View.NO_ID
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
        currentVoteRow = null
        currentVoteText = null
        changeVoteBtn = null
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
            scanRepository.getCommunityOpinions(scanId, TelemetryIds.userPseudoId(ctx), context = ctx)
                .onSuccess {
                    render(it.consensus, it.opinions)
                    renderLike(it.likeCount, it.likedByMe)
                    renderVoteState(it.myOpinion)
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
            if (!requireAuthenticatedAction()) return@setOnClickListener
            stopRequestBtn?.isEnabled = false
            val ctx = requireContext()
            viewLifecycleOwner.lifecycleScope.launch {
                scanRepository
                    .cancelSecondOpinion(scanId)
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

    private fun renderLike(count: Int, likedByMe: Boolean) {
        liked = likedByMe
        // Text stays set even at zero (the optimistic-update read-back in
        // setupLike parses it back with toIntOrNull()) — only visibility
        // hides the "0" a bare heart icon reads better without.
        likeCountView?.text = count.toString()
        likeCountView?.visibility = if (count > 0) View.VISIBLE else View.GONE
        likeIcon?.setImageResource(if (likedByMe) R.drawable.ic_heart_filled else R.drawable.ic_heart)
        likeIcon?.imageTintList = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(
                requireContext(),
                if (likedByMe) R.color.primary else R.color.on_surface_secondary,
            ),
        )
    }

    /** Agree/Disagree vs. "Your opinion: X — Change opinion": the row the
     *  viewer sees depends on whether the server already has a pick from
     *  them (opinions_for's my_opinion), since a second vote replaces the
     *  first server-side rather than adding to it — presenting Agree/Disagree
     *  again would offer an action whose real effect is hidden. Only touches
     *  the two rows; [showPicker] governs the chips/note/Send underneath. */
    private fun renderVoteState(opinion: String?) {
        val current = opinion?.takeIf { it.isNotBlank() }
        myOpinion = current
        voteRow?.visibility = if (current == null) View.VISIBLE else View.GONE
        currentVoteRow?.visibility = if (current == null) View.GONE else View.VISIBLE
        if (current != null) {
            val label = DiseaseLabels.displayName(current, LocaleHelper.isRussian(requireContext())) ?: current
            currentVoteText?.text = getString(R.string.community_opinion_my_vote, label)
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

    /** Post one opinion (agree = the model's diagnosis, or a picked disease)
     * and lock the input. The server derives attribution from the signed token. */
    private fun submitDisease(key: String, note: String? = null) {
        if (key.isBlank()) return
        val ctx = requireContext()
        if (!requireAuthenticatedAction()) return
        Haptics.tick(ctx)
        sendBtn?.isEnabled = false
        view?.findViewById<MaterialButton>(R.id.btn_opinion_agree)?.isEnabled = false
        view?.findViewById<MaterialButton>(R.id.btn_opinion_disagree)?.isEnabled = false

        val request = CommunityOpinionRequest(
            scanId = scanId,
            suggestedDisease = key,
            note = note,
        )
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.submitCommunityOpinion(ctx, request)
                .onSuccess { resp ->
                    Toast.makeText(ctx, R.string.community_opinion_thanks, Toast.LENGTH_SHORT).show()
                    // One opinion per signed actor is enforced server-side.
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

    /** Opens login and does not replay the mutation after authentication. */
    private fun requireAuthenticatedAction(): Boolean {
        if (authRepository.state.value is AuthState.Authenticated) return true
        val ctx = requireContext()
        Toast.makeText(ctx, R.string.community_sign_in_required, Toast.LENGTH_SHORT).show()
        startActivity(com.plantdiseases.app.ui.account.AccountActivity.intent(ctx))
        return false
    }

    private fun showReportDialog(targetType: String, targetId: String) {
        if (!requireAuthenticatedAction()) return
        val labels = resources.getStringArray(R.array.community_report_reasons)
        var selected = -1
        val dialog = MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.community_report_title)
            .setMessage(R.string.community_report_prompt)
            .setSingleChoiceItems(labels, -1) { _, which -> selected = which }
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_report_content, null)
            .create()
        dialog.setOnShowListener {
            val submit = dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE)
            submit.isEnabled = false
            dialog.listView.setOnItemClickListener { _, _, position, _ ->
                selected = position
                submit.isEnabled = position in REPORT_REASON_KEYS.indices
            }
            submit.setOnClickListener {
                val reason = REPORT_REASON_KEYS.getOrNull(selected) ?: return@setOnClickListener
                dialog.dismiss()
                submitReport(targetType, targetId, reason)
            }
        }
        dialog.show()
    }

    private fun submitReport(targetType: String, targetId: String, reason: String) {
        val ctx = requireContext()
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.reportCommunityContent(targetType, targetId, reason)
                .onSuccess {
                    Toast.makeText(ctx, R.string.community_report_sent, Toast.LENGTH_SHORT).show()
                }
                .onFailure {
                    Toast.makeText(ctx, R.string.community_safety_error, Toast.LENGTH_SHORT).show()
                }
        }
    }

    private fun confirmBlock(targetType: String, targetId: String) {
        if (!requireAuthenticatedAction()) return
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.community_block_title)
            .setMessage(R.string.community_block_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_block_confirm) { _, _ ->
                blockAuthor(targetType, targetId)
            }
            .show()
    }

    private fun blockAuthor(targetType: String, targetId: String) {
        val ctx = requireContext()
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.blockCommunityAuthor(targetType, targetId)
                .onSuccess {
                    Toast.makeText(ctx, R.string.community_author_blocked, Toast.LENGTH_SHORT).show()
                    parentFragmentManager.setFragmentResult(
                        CONTENT_BLOCKED,
                        bundleOf("target_type" to targetType, "target_id" to targetId),
                    )
                    if (targetType == TARGET_SCAN) {
                        dismissAllowingStateLoss()
                    } else {
                        loadOpinions()
                    }
                }
                .onFailure {
                    Toast.makeText(ctx, R.string.community_safety_error, Toast.LENGTH_SHORT).show()
                }
        }
    }

    /** Hide the picker / note / send and show the "already opined" note —
     *  used once the signed actor has left their single opinion on the scan. */
    private fun lockInputControls() {
        val v = view ?: return
        noteInput?.text?.clear()
        chipGroup?.clearCheck()
        showPicker(v, visible = false)
        v.findViewById<MaterialButton>(R.id.btn_opinion_agree).isEnabled = true
        v.findViewById<MaterialButton>(R.id.btn_opinion_disagree).isEnabled = true
        sendBtn?.isEnabled = false
        renderVoteState(justSubmittedKey)
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
            opinions.forEach {
                list.addView(
                    com.plantdiseases.app.ui.community.OpinionViews.buildOpinionRow(requireContext(), it),
                )
            }
        }
    }

    /** Draw the vote graph: one horizontal percentage bar per disease,
     *  sorted high-to-low. Hidden entirely until at least one vote exists. */
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
        val ctx = requireContext()
        consensus
            .sortedByDescending { it.count }
            .forEachIndexed { index, entry ->
                graph.addView(
                    com.plantdiseases.app.ui.community.OpinionViews.buildVoteBar(
                        ctx,
                        entry,
                        total,
                        leading = index == 0,
                    ),
                )
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

        op.contentId?.takeIf { it.isNotBlank() && !op.isMine }?.let { contentId ->
            val actions = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.END
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(2) }
            }
            actions.addView(
                MaterialButton(ctx).apply {
                    minWidth = 0
                    backgroundTintList =
                        android.content.res.ColorStateList.valueOf(android.graphics.Color.TRANSPARENT)
                    text = getString(R.string.community_report_content)
                    isAllCaps = false
                    setOnClickListener { showReportDialog(TARGET_OPINION, contentId) }
                },
            )
            actions.addView(
                MaterialButton(ctx).apply {
                    minWidth = 0
                    backgroundTintList =
                        android.content.res.ColorStateList.valueOf(android.graphics.Color.TRANSPARENT)
                    text = getString(R.string.community_block_author)
                    isAllCaps = false
                    setTextColor(ContextCompat.getColor(ctx, R.color.disease_red))
                    setOnClickListener { confirmBlock(TARGET_OPINION, contentId) }
                },
            )
            col.addView(actions)
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
        private const val TARGET_SCAN = "scan"
        private const val TARGET_OPINION = "opinion"

        // True ONLY for the scan owner's view (showForOwner) — gates the
        // request-status row + "Stop request" action. Distinct from
        // ARG_VIEW_ONLY, which read-only viewers of OTHER people's scans
        // also set.
        private const val ARG_OWNER = "arg_owner"

        /** Fragment-result key fired when the owner changes the request's
         *  state from inside the sheet (stop) — the result screen listens
         *  and re-renders its second-opinion control. */
        const val REQUEST_STATE_CHANGED = "second_opinion_state_changed"
        const val CONTENT_BLOCKED = "community_content_blocked"

        private val REPORT_REASON_KEYS = listOf(
            "sexual_content",
            "violence_or_threats",
            "hate_or_harassment",
            "illegal_or_dangerous",
            "spam_or_misleading",
            "privacy_or_impersonation",
            "other_rules_violation",
        )

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
    }
}
