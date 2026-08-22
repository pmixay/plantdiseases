package com.plantdiseases.app.ui.result

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import androidx.core.content.ContextCompat
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
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityTopDisease
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.common.SpeciesInfoCardView
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
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
    private var lockedCard: View? = null
    private var dockContainer: View? = null
    private var composerRow: View? = null
    private var chipScroll: HorizontalScrollView? = null
    private var confidenceBar: LinearProgressIndicator? = null
    private var speciesCard: SpeciesInfoCardView? = null
    private var opinionsOpen = false

    /** Whether the dock is showing the composer rather than a vote row. Kept
     *  separate from the vote state so a background reload can refresh the
     *  consensus without yanking a half-written note out from under the user. */
    private var composerOpen = false

    // Local mirror of the server's liked_by_me — updated optimistically on
    // tap and by every server response that actually carries it (loadOpinions
    // only; the opinion-submit response doesn't pass user_pseudo_id, so it
    // can't tell "liked" apart from "unknown" and must not overwrite this).
    private var liked = false
    private var likeInFlight = false

    /** True while the like/⋯ pair is overlaid on the scan photo, which decides
     *  their resting tint (on_hero over the image, on_surface_secondary over
     *  the sheet background). */
    private var actionsOnPhoto = true

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
        opinionsOpen = arguments?.getBoolean(ARG_WRITABLE, false) == true
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
        lockedCard = view.findViewById(R.id.card_opinion_locked)
        dockContainer = view.findViewById(R.id.ll_opinion_dock)
        composerRow = view.findViewById(R.id.ll_opinion_composer)
        chipScroll = view.findViewById(R.id.scroll_opinion_chips)
        confidenceBar = view.findViewById(R.id.progress_opinion_confidence)
        speciesCard = view.findViewById(R.id.card_opinion_species)

        val currentCard = view.findViewById<View>(R.id.card_opinion_current)
        val currentView = view.findViewById<TextView>(R.id.tv_opinion_current)
        currentName?.takeIf { it.isNotBlank() }
            ?.let { currentView.text = it }
            ?: run { currentCard.visibility = View.GONE }
        renderMeta()
        renderPhoto(view)
        bindSpecies()

        val ctx = requireContext()
        // Commenting on your own scan makes no sense — that case is
        // MyScanRepliesActivity now, a separate screen this sheet never
        // opens for. Every caller reaching this sheet is a genuine viewer,
        // and Community v2 dropped the "already opined" lock: any viewer
        // may leave more than one comment.
        setupLike(ctx)
        setupSafety(view)

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

        // Agree and Disagree both open the same composer — Agree just
        // pre-selects the model's own class, so agreeing without a note is
        // Agree then Post, and everyone gets a chance to actually write
        // something before sending. An instant-submit Agree was the reason
        // "leave a comment" read as "click Agree or Disagree, nothing else":
        // there was never a text box in that path.
        composerOpen = false
        sendBtn?.isEnabled = false
        chipGroup?.setOnCheckedStateChangeListener { group, _ ->
            sendBtn?.isEnabled = group.checkedChipId != View.NO_ID
        }
        view.findViewById<MaterialButton>(R.id.btn_opinion_agree).setOnClickListener {
            openComposer(preselect = diseaseKey)
        }
        view.findViewById<MaterialButton>(R.id.btn_opinion_disagree).setOnClickListener {
            openComposer(preselect = null)
        }
        changeVoteBtn?.setOnClickListener { openComposer(preselect = myOpinion) }
        // The composer's own way out. With this here the sheet needs no Cancel
        // button: swipe, scrim tap and Back already dismiss the sheet itself.
        view.findViewById<View>(R.id.btn_opinion_composer_close).setOnClickListener {
            closeComposer()
        }
        sendBtn?.setOnClickListener { submitFromPicker() }
        // The feed's flag is only a display hint. Keep every mutation locked
        // until the thread endpoint confirms the request is still live.
        applyOpinionAvailability(false)
        likeRow?.isEnabled = false
        likeRow?.alpha = 0.5f

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
        // MATCH_PARENT + maxHeight, not a fixed pixel height: the cap still
        // holds the sheet at 90% of the screen, but when the keyboard resizes
        // the window the sheet shrinks with it instead of keeping a height
        // taller than the window and pushing its own bottom — now the dock,
        // with the note field and Post — off screen.
        sheet.layoutParams = sheet.layoutParams.apply {
            height = ViewGroup.LayoutParams.MATCH_PARENT
        }
        behavior.maxHeight = maxHeight
        behavior.peekHeight = maxHeight
        behavior.skipCollapsed = true
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
        // Deprecated in API 30 with no like-for-like constant: the documented
        // replacement is edge-to-edge plus IME inset handling, which this app
        // deliberately has not adopted (targetSdk is held at 35 for exactly
        // that reason). The sheet has a text field, so losing the resize would
        // put the keyboard over the input — keep the working behaviour.
        @Suppress("DEPRECATION")
        dialog?.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
    }

    /** Model confidence, as a number AND a meter — the same pairing the
     *  scan-result screen uses, so the sheet reads like a real result rather
     *  than a sentence under a decorative icon. Both hidden when unknown. */
    private fun renderMeta() {
        val meta = metaView ?: return
        val confidence = arguments?.getFloat(ARG_CONFIDENCE, -1f) ?: -1f
        if (confidence in 0f..1f) {
            val percent = Math.round(confidence * 100)
            meta.visibility = View.VISIBLE
            meta.text = getString(R.string.community_opinion_confidence, percent)
            confidenceBar?.visibility = View.VISIBLE
            confidenceBar?.setProgressCompat(percent, false)
        } else {
            meta.visibility = View.GONE
            confidenceBar?.visibility = View.GONE
        }
    }

    /** The one shared species card. Community v2 dropped it here on the
     *  grounds that the photo is the subject; it's back because knowing WHAT
     *  the plant is changes what you'd call the problem. Hides itself when the
     *  feed item carried no species, so no pre-check is needed. */
    private fun bindSpecies() {
        val card = speciesCard ?: return
        card.bind(
            viewLifecycleOwner.lifecycleScope,
            scientific = arguments?.getString(ARG_SPECIES),
            common = null,
        )
    }

    /** Show the shared scan photo (borrowed from the scan-result screen) when
     *  the feed item carried a thumbnail; otherwise leave the slot hidden. */
    private fun renderPhoto(view: View) {
        val card = view.findViewById<View>(R.id.card_opinion_photo)
        val photo = view.findViewById<ImageView>(R.id.iv_opinion_photo)
        val thumb = arguments?.getString(ARG_THUMB)?.takeIf { it.isNotBlank() }
        if (thumb == null) {
            // No photo to overlay, so the glass treatment has nothing behind it
            // and the actions bar becomes an ordinary row of solid controls.
            card.visibility = View.GONE
            view.findViewById<View>(R.id.ll_opinion_like)
                .setBackgroundResource(R.drawable.bg_opinion_like_pill)
            view.findViewById<View>(R.id.btn_opinion_more)
                .setBackgroundResource(R.drawable.bg_circle_surface_variant)
            actionsOnPhoto = false
            tintActions(view, R.color.on_surface_secondary)
            return
        }
        actionsOnPhoto = true
        card.visibility = View.VISIBLE
        Glide.with(this)
            .load("${ServerConfig.baseUrl}api/community/thumb/$thumb")
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .transform(CenterCrop(), RoundedCorners(24))
            .into(photo)
    }

    /** Keeps the like icon, its count and the ⋯ on one tint, so the pair reads
     *  as a set whether it sits on the photo (on_hero) or on the sheet
     *  background (on_surface_secondary). [renderLike] re-tints the heart
     *  afterwards when this viewer has liked the scan. */
    private fun tintActions(view: View, colorRes: Int) {
        val color = ContextCompat.getColor(requireContext(), colorRes)
        val tint = android.content.res.ColorStateList.valueOf(color)
        view.findViewById<ImageView>(R.id.iv_opinion_like).imageTintList = tint
        view.findViewById<ImageView>(R.id.btn_opinion_more).imageTintList = tint
        view.findViewById<TextView>(R.id.tv_opinion_like_count).setTextColor(color)
    }

    /** Check the chip matching [key] (the model's own verdict, for the Agree
     *  shortcut) and enable Send immediately — the user doesn't have to
     *  re-tap a chip they already implicitly picked by hitting Agree. */
    private fun preselectChip(key: String) {
        val group = chipGroup ?: return
        (0 until group.childCount)
            .map { group.getChildAt(it) as Chip }
            .firstOrNull { it.tag == key }
            ?.let { chip ->
                group.check(chip.id)
                // The picker is one horizontally scrolling row, so a chip
                // near the end starts off-screen and the selection reads as
                // "nothing picked" until you happen to scroll.
                chipScroll?.post { chipScroll?.smoothScrollTo(chip.left, 0) }
            }
        sendBtn?.isEnabled = group.checkedChipId != View.NO_ID
    }

    /**
     * The dock shows exactly one of three faces, and this is the only place
     * that decides which: the composer if it's open, otherwise "your opinion +
     * change" once the server has a pick from this viewer, otherwise
     * Agree/Disagree. A scan that isn't accepting opinions hides the dock
     * entirely — its replies stay readable above.
     */
    private fun applyDockState() {
        val dock = dockContainer ?: return
        if (!opinionsOpen) {
            composerOpen = false
            dock.visibility = View.GONE
            return
        }
        dock.visibility = View.VISIBLE
        composerRow?.visibility = if (composerOpen) View.VISIBLE else View.GONE
        voteRow?.visibility =
            if (!composerOpen && myOpinion == null) View.VISIBLE else View.GONE
        currentVoteRow?.visibility =
            if (!composerOpen && myOpinion != null) View.VISIBLE else View.GONE
    }

    /** Agree, Disagree and Change all land here; only the pre-selected chip
     *  differs. Agree pre-selects the model's own verdict, so agreeing with a
     *  note stays two taps. */
    private fun openComposer(preselect: String?) {
        composerOpen = true
        applyDockState()
        preselect?.takeIf { it.isNotBlank() }?.let { preselectChip(it) }
    }

    private fun closeComposer() {
        composerOpen = false
        chipGroup?.clearCheck()
        noteInput?.text?.clear()
        sendBtn?.isEnabled = false
        applyDockState()
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
        lockedCard = null
        dockContainer = null
        composerRow = null
        chipScroll = null
        confidenceBar = null
        speciesCard = null
    }

    private fun loadOpinions() {
        val ctx = requireContext()
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.getCommunityOpinions(scanId, TelemetryIds.userPseudoId(ctx), context = ctx)
                .onSuccess {
                    emptyView?.apply {
                        text = getString(R.string.community_opinion_empty)
                        isClickable = false
                        setOnClickListener(null)
                    }
                    render(it.consensus, it.opinions)
                    renderLike(it.likeCount, it.likedByMe)
                    renderVoteState(it.myOpinion)
                    applyOpinionAvailability(it.wantsOpinion)
                    likeRow?.isEnabled = true
                    likeRow?.alpha = 1f
                }
                .onFailure {
                    render(emptyList(), emptyList())
                    applyOpinionAvailability(false)
                    likeRow?.isEnabled = false
                    likeRow?.alpha = 0.5f
                    emptyView?.apply {
                        visibility = View.VISIBLE
                        text = getString(R.string.community_opinion_error)
                        isClickable = true
                        setOnClickListener {
                            text = getString(R.string.community_opinion_loading)
                            loadOpinions()
                        }
                    }
                }
        }
    }

    private fun setupLike(ctx: Context) {
        likeRow?.setOnClickListener {
            if (likeInFlight) return@setOnClickListener
            val previousCount = likeCountView?.text?.toString()?.toIntOrNull() ?: 0
            val previousLiked = liked
            val next = !liked
            likeInFlight = true
            Haptics.tick(ctx)
            renderLike((previousCount + if (next) 1 else -1).coerceAtLeast(0), next)
            viewLifecycleOwner.lifecycleScope.launch {
                scanRepository.setCommunityLike(
                    scanId,
                    TelemetryIds.userPseudoId(ctx),
                    next,
                    ctx,
                )
                    .onSuccess { renderLike(it.count, it.liked) }
                    .onFailure { renderLike(previousCount, previousLiked) }
                likeInFlight = false
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
        val restingTint = if (actionsOnPhoto) R.color.on_hero else R.color.on_surface_secondary
        likeIcon?.imageTintList = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(
                requireContext(),
                if (likedByMe) R.color.primary else restingTint,
            ),
        )
    }

    /** Agree/Disagree vs. "Your opinion: X — Change opinion": the face the
     *  viewer sees depends on whether the server already has a pick from
     *  them (opinions_for's my_opinion), since a second vote replaces the
     *  first server-side rather than adding to it — presenting Agree/Disagree
     *  again would offer an action whose real effect is hidden. Records the
     *  pick and its label; [applyDockState] decides what the dock shows. */
    private fun renderVoteState(opinion: String?) {
        val current = opinion?.takeIf { it.isNotBlank() }
        myOpinion = current
        if (current != null) {
            val label = DiseaseLabels.displayName(current, LocaleHelper.isRussian(requireContext())) ?: current
            currentVoteText?.text = getString(R.string.community_opinion_my_vote, label)
        }
        applyDockState()
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
        if (key.isBlank() || !opinionsOpen) return
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
            scanRepository.submitCommunityOpinion(ctx, request)
                .onSuccess { resp ->
                    Toast.makeText(ctx, R.string.community_opinion_thanks, Toast.LENGTH_SHORT).show()
                    resetInputAfterSubmit(key)
                    render(resp.consensus, resp.opinions)
                }
                .onFailure { error ->
                    if (error is ScanRepository.CommunityOpinionsLockedException) {
                        applyOpinionAvailability(false)
                        Toast.makeText(ctx, R.string.community_opinion_locked_toast, Toast.LENGTH_SHORT).show()
                    } else {
                        Toast.makeText(ctx, R.string.community_opinion_error, Toast.LENGTH_SHORT).show()
                        sendBtn?.isEnabled = true
                        view?.findViewById<MaterialButton>(R.id.btn_opinion_agree)?.isEnabled = true
                        view?.findViewById<MaterialButton>(R.id.btn_opinion_disagree)?.isEnabled = true
                    }
                }
        }
    }

    /** After a successful post: clear the note + chip pick, hide the picker,
     *  re-enable both vote buttons (for if the row comes back later — see
     *  [renderVoteState]), and switch to "Your opinion: X — Change opinion"
     *  for [justSubmittedKey] rather than bringing Agree/Disagree back, since
     *  the server now has a pick from this viewer. */
    private fun resetInputAfterSubmit(justSubmittedKey: String) {
        val v = view ?: return
        closeComposer()
        v.findViewById<MaterialButton>(R.id.btn_opinion_agree).isEnabled = true
        v.findViewById<MaterialButton>(R.id.btn_opinion_disagree).isEnabled = true
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
                    com.plantdiseases.app.ui.community.OpinionViews.buildOpinionRow(
                        requireContext(),
                        it,
                        onReport = { opinion ->
                            opinion.contentId?.let { id -> showReportDialog("opinion", id) }
                        },
                        onBlock = { opinion ->
                            opinion.contentId?.let { id -> confirmBlock("opinion", id) }
                        },
                    ),
                )
            }
        }
    }

    /** The server response is authoritative. A request can expire or be
     * cancelled while this sheet is open, so every successful read and every
     * 423 submit race re-applies this state. Existing replies remain visible. */
    private fun applyOpinionAvailability(open: Boolean) {
        opinionsOpen = open
        lockedCard?.visibility = if (open) View.GONE else View.VISIBLE
        applyDockState()
    }

    /** Report and Block for the SCAN itself. They used to be two accent text
     *  buttons carrying the same visual weight as the action the viewer came
     *  for; behind one ⋯ they stay one tap away without competing. Individual
     *  replies keep their own controls on each row. */
    private fun setupSafety(root: View) {
        root.findViewById<View>(R.id.btn_opinion_more).setOnClickListener { anchor ->
            PopupMenu(requireContext(), anchor).apply {
                menu.add(0, MENU_REPORT, 0, getString(R.string.community_report_action))
                menu.add(0, MENU_BLOCK, 1, getString(R.string.community_block_action))
                setOnMenuItemClickListener { item ->
                    when (item.itemId) {
                        MENU_REPORT -> {
                            showReportDialog("scan", scanId)
                            true
                        }
                        MENU_BLOCK -> {
                            confirmBlock("scan", scanId)
                            true
                        }
                        else -> false
                    }
                }
                show()
            }
        }
    }

    private fun showReportDialog(targetType: String, targetId: String) {
        val labels = resources.getStringArray(R.array.community_report_reason_labels)
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.community_report_title)
            .setItems(labels) { _, which ->
                val reason = REPORT_REASONS.getOrNull(which) ?: return@setItems
                viewLifecycleOwner.lifecycleScope.launch {
                    scanRepository.reportCommunityContent(
                        TelemetryIds.userPseudoId(requireContext()),
                        targetType,
                        targetId,
                        reason,
                        requireContext(),
                    ).onSuccess {
                        Toast.makeText(requireContext(), R.string.community_report_sent, Toast.LENGTH_SHORT).show()
                    }.onFailure {
                        Toast.makeText(requireContext(), R.string.community_safety_error, Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun confirmBlock(targetType: String, targetId: String) {
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.community_block_title)
            .setMessage(R.string.community_block_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_block_confirm) { _, _ ->
                viewLifecycleOwner.lifecycleScope.launch {
                    scanRepository.blockCommunityAuthor(
                        TelemetryIds.userPseudoId(requireContext()),
                        targetType,
                        targetId,
                        requireContext(),
                    ).onSuccess {
                        Toast.makeText(requireContext(), R.string.community_blocked, Toast.LENGTH_SHORT).show()
                        dismissAllowingStateLoss()
                    }.onFailure {
                        Toast.makeText(requireContext(), R.string.community_safety_error, Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .show()
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

    companion object {
        private const val TAG = "community-opinion-sheet"
        private const val ARG_SCAN_ID = "arg_scan_id"
        private const val ARG_CURRENT_NAME = "arg_current_name"
        private const val ARG_DISEASE_KEY = "arg_disease_key"
        private const val ARG_CONFIDENCE = "arg_confidence"
        private const val ARG_THUMB = "arg_thumb"
        private const val ARG_SPECIES = "arg_species"
        private const val ARG_WRITABLE = "arg_writable"

        private const val MENU_REPORT = 1
        private const val MENU_BLOCK = 2

        private val REPORT_REASONS = listOf(
            "sexual_content",
            "violence_or_threats",
            "hate_or_harassment",
            "illegal_or_dangerous",
            "spam_or_misleading",
            "privacy_or_impersonation",
            "other_rules_violation",
        )

        /** Diagnosable classes a responder can pick, as the chip picker's
         *  key → string-resource list. NOT a display catalogue: rendering a
         *  class key anywhere in the thread or the vote graph goes through
         *  [com.plantdiseases.app.util.DiseaseLabels] (via OpinionViews), so
         *  the sheet and MyScanRepliesActivity word every diagnosis the same.
         *  Keys must be present in the server's DISEASES_DATABASE or the POST
         *  is rejected (422). ``not_a_plant`` is included so a responder can
         *  flag that the shared photo isn't a plant at all — it's a valid
         *  class in both the classifier head and the server database. Labels
         *  reuse the feedback-correction strings. */
        private val CLASS_CATALOG: List<Pair<String, Int>> = listOf(
            "healthy" to R.string.feedback_class_healthy,
            "leaf_spot" to R.string.feedback_class_leaf_spot,
            "powdery_mildew" to R.string.feedback_class_powdery_mildew,
            "gray_mold_botrytis" to R.string.feedback_class_gray_mold_botrytis,
            "leaf_scorch_underwatering" to R.string.feedback_class_leaf_scorch_underwatering,
            "nutrient_deficiency" to R.string.feedback_class_nutrient_deficiency,
            "viral_disease" to R.string.feedback_class_viral_disease,
            "pests" to R.string.feedback_class_pests,
            "edema" to R.string.feedback_class_edema,
            "sunburn_or_scorch" to R.string.feedback_class_sunburn_or_scorch,
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
                arguments = Bundle().apply {
                    putString(ARG_SCAN_ID, item.scanId)
                    putString(ARG_CURRENT_NAME, currentDisplayName)
                    putString(ARG_DISEASE_KEY, item.disease)
                    putFloat(ARG_CONFIDENCE, item.confidence ?: -1f)
                    putString(ARG_THUMB, item.thumbPath)
                    putString(ARG_SPECIES, item.species)
                    putBoolean(ARG_WRITABLE, item.wantsOpinion)
                }
            }.show(fm, TAG)
        }
    }
}
