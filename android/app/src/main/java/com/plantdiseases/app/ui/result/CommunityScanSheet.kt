package com.plantdiseases.app.ui.result

import android.content.res.ColorStateList
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
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
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.account.QuickSignIn
import com.plantdiseases.app.ui.common.SpeciesInfoCardView
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ServerConfig
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * One shared community scan: the photo, the model's diagnosis and confidence,
 * the species card and the like control, plus report and block for another
 * member's scan. Opened from the map, community profiles, the result screen's
 * nearby scans and the species page. A guest sees the like locked (gray) and
 * no report or block; a tap offers sign-in in place ([QuickSignIn]).
 *
 * State-light and recreation-safe: the only inputs are stashed in arguments,
 * so a process-death restore reloads cleanly instead of crashing.
 */
@AndroidEntryPoint
class CommunityScanSheet : BottomSheetDialogFragment() {

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var authRepository: AuthRepository

    private var scanId: String = ""
    private var mine = false
    private var likeIcon: ImageView? = null
    private var likeCountView: TextView? = null
    private var safetyRow: View? = null

    // A guest signs in from a sheet over this one; the controls unlock in place.
    private val quickSignIn = QuickSignIn.of(this) { applyGuestState() }
    private val isGuest: Boolean get() = authRepository.state.value !is AuthState.Authenticated

    // Local mirror of the feed item's liked_by_me, updated optimistically on tap.
    private var liked = false
    private var likeCount = 0
    private var likeInFlight = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.sheet_community_scan, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val args = arguments
        scanId = args?.getString(ARG_SCAN_ID).orEmpty()
        if (scanId.isBlank()) {
            dismissAllowingStateLoss()
            return
        }
        mine = args?.getBoolean(ARG_MINE, false) == true
        liked = args?.getBoolean(ARG_LIKED, false) == true
        likeCount = args?.getInt(ARG_LIKE_COUNT, 0) ?: 0

        val currentCard = view.findViewById<View>(R.id.card_scan_current)
        val currentView = view.findViewById<TextView>(R.id.tv_scan_current)
        args?.getString(ARG_CURRENT_NAME)?.takeIf { it.isNotBlank() }
            ?.let { currentView.text = it }
            ?: run { currentCard.visibility = View.GONE }
        renderMeta(view)
        renderPhoto(view)

        safetyRow = view.findViewById(R.id.ll_community_scan_safety)
        view.findViewById<MaterialButton>(R.id.btn_community_report_scan)
            ?.setOnClickListener { showReportDialog() }
        view.findViewById<MaterialButton>(R.id.btn_community_block_scan_author)
            ?.setOnClickListener { confirmBlock() }

        likeIcon = view.findViewById(R.id.iv_scan_like)
        likeCountView = view.findViewById(R.id.tv_scan_like_count)
        // Liking your own scan makes no sense: the pill is hidden for it.
        val likeRow = view.findViewById<View>(R.id.ll_scan_like)
        likeRow.visibility = if (mine) View.GONE else View.VISIBLE
        likeRow.setOnClickListener { toggleLike() }

        applyGuestState()
        expandSheet(view)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        likeIcon = null
        likeCountView = null
        safetyRow = null
    }

    /** Report and block are for members, on someone else's scan. */
    private fun applyGuestState() {
        safetyRow?.visibility = if (isGuest || mine) View.GONE else View.VISIBLE
        renderLike()
    }

    /** Open expanded, not collapsed: the photo is the tallest element, so at
     *  the default peek height the diagnosis and like landed off-screen. */
    private fun expandSheet(view: View) {
        val sheet = view.parent as? View ?: return
        val behavior = BottomSheetBehavior.from(sheet)
        behavior.skipCollapsed = true
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
    }

    /** Model confidence line ("Confidence 87%"), hidden when unknown. */
    private fun renderMeta(view: View) {
        val meta = view.findViewById<TextView>(R.id.tv_scan_meta)
        val confidence = arguments?.getFloat(ARG_CONFIDENCE, -1f) ?: -1f
        if (confidence in 0f..1f) {
            meta.visibility = View.VISIBLE
            meta.text = getString(R.string.community_scan_confidence, Math.round(confidence * 100))
        } else {
            meta.visibility = View.GONE
        }
        val species = arguments?.getString(ARG_SPECIES)?.takeIf { it.isNotBlank() }
        view.findViewById<SpeciesInfoCardView>(R.id.card_scan_species).bind(species, null)
    }

    /** The shared scan photo when the feed item carried a thumbnail. */
    private fun renderPhoto(view: View) {
        val card = view.findViewById<View>(R.id.card_scan_photo)
        val photo = view.findViewById<ImageView>(R.id.iv_scan_photo)
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
            .transform(CenterCrop(), RoundedCorners(PHOTO_CORNER_PX))
            .into(photo)
    }

    private fun toggleLike() {
        if (likeInFlight || !quickSignIn.require()) return
        val previousCount = likeCount
        val previousLiked = liked
        val next = !liked
        likeInFlight = true
        Haptics.tick(requireContext())
        liked = next
        likeCount = (previousCount + if (next) 1 else -1).coerceAtLeast(0)
        renderLike()
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.setCommunityLike(scanId, next)
                .onSuccess {
                    liked = it.liked
                    likeCount = it.count
                }
                .onFailure {
                    liked = previousLiked
                    likeCount = previousCount
                }
            renderLike()
            likeInFlight = false
        }
    }

    private fun renderLike() {
        val icon = likeIcon ?: return
        val count = likeCountView ?: return
        count.text = likeCount.toString()
        count.visibility = if (likeCount > 0) View.VISIBLE else View.GONE
        icon.setImageResource(if (liked) R.drawable.ic_heart_filled else R.drawable.ic_heart)
        val color = ContextCompat.getColor(
            icon.context,
            when {
                isGuest -> R.color.guest_locked
                liked -> R.color.primary
                else -> R.color.on_surface
            },
        )
        icon.imageTintList = ColorStateList.valueOf(color)
        count.setTextColor(if (isGuest) color else ContextCompat.getColor(icon.context, R.color.on_surface))
    }

    private fun showReportDialog() {
        if (!quickSignIn.require()) return
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
                viewLifecycleOwner.lifecycleScope.launch {
                    scanRepository.reportCommunityContent(TARGET_SCAN, scanId, reason)
                        .onSuccess { toast(R.string.community_report_sent) }
                        .onFailure { toast(R.string.community_safety_error) }
                }
            }
        }
        dialog.show()
    }

    private fun confirmBlock() {
        if (!quickSignIn.require()) return
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.community_block_title)
            .setMessage(R.string.community_block_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_block_confirm) { _, _ ->
                viewLifecycleOwner.lifecycleScope.launch {
                    scanRepository.blockCommunityAuthor(TARGET_SCAN, scanId)
                        .onSuccess {
                            toast(R.string.community_author_blocked)
                            parentFragmentManager.setFragmentResult(
                                CONTENT_BLOCKED,
                                Bundle().apply {
                                    putString("target_type", TARGET_SCAN)
                                    putString("target_id", scanId)
                                },
                            )
                            dismissAllowingStateLoss()
                        }
                        .onFailure { toast(R.string.community_safety_error) }
                }
            }
            .show()
    }

    private fun toast(message: Int) {
        Toast.makeText(requireContext(), message, Toast.LENGTH_SHORT).show()
    }

    companion object {
        private const val TAG = "community-scan-sheet"
        private const val ARG_SCAN_ID = "arg_scan_id"
        private const val ARG_CURRENT_NAME = "arg_current_name"
        private const val ARG_CONFIDENCE = "arg_confidence"
        private const val ARG_THUMB = "arg_thumb"
        private const val ARG_SPECIES = "arg_species"
        private const val ARG_LIKED = "arg_liked"
        private const val ARG_LIKE_COUNT = "arg_like_count"
        private const val ARG_MINE = "arg_mine"
        private const val TARGET_SCAN = "scan"
        private const val PHOTO_CORNER_PX = 24

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

        /** Open a feed/carousel item. [mine]: the user's own scan (no like,
         *  report or block). */
        fun show(
            fm: FragmentManager,
            item: CommunityScanItem,
            currentDisplayName: String?,
            mine: Boolean = false,
        ) {
            CommunityScanSheet().apply {
                arguments = Bundle().apply {
                    putString(ARG_SCAN_ID, item.scanId)
                    putString(ARG_CURRENT_NAME, currentDisplayName)
                    putFloat(ARG_CONFIDENCE, item.confidence ?: -1f)
                    putString(ARG_THUMB, item.thumbPath)
                    putString(ARG_SPECIES, item.species)
                    putBoolean(ARG_LIKED, item.likedByMe)
                    putInt(ARG_LIKE_COUNT, item.likeCount)
                    putBoolean(ARG_MINE, mine)
                }
            }.show(fm, TAG)
        }
    }
}
