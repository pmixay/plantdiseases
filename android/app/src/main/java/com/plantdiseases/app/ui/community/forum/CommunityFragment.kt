package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.chip.Chip
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.model.StaffMe
import com.plantdiseases.app.data.repository.CommunityAvatarSync
import com.plantdiseases.app.data.repository.ForumNotificationSync
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.databinding.FragmentCommunityBinding
import com.plantdiseases.app.databinding.SheetForumFiltersBinding
import com.plantdiseases.app.ui.account.QuickSignIn
import com.plantdiseases.app.ui.community.CommunityMapActivity
import com.plantdiseases.app.util.applyStatusBarTopInset
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The Community tab: the forum feed, search, recent activity and the Ask
 * button. What to show (For you / Needs help / Solved / Mine), the space and
 * the order are chosen in a filter sheet; only the filters that differ from
 * the default show under the header, each removable. The older map and
 * nearby-scans screens are in the overflow menu.
 */
@AndroidEntryPoint
class CommunityFragment : Fragment() {

    @Inject
    lateinit var forum: ForumRepository

    @Inject
    lateinit var forumSync: ForumNotificationSync

    @Inject
    lateinit var avatarSync: CommunityAvatarSync

    private var _binding: FragmentCommunityBinding? = null
    private val binding get() = _binding!!

    // Guests sign in from a sheet over the feed, then carry on here.
    private val quickSignIn = QuickSignIn.of(this) { onSignedIn() }

    private lateinit var adapter: ForumPostAdapter
    private var tab = TAB_ALL
    private var sort = SORT_HOT
    private var space: String? = null
    private var spaces: List<ForumSpace> = emptyList()
    private var nextCursor: String? = null
    private var loadJob: Job? = null
    private var staff: StaffMe? = null
    private var photoPromptShowing = false

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentCommunityBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.root.applyStatusBarTopInset()
        adapter = ForumPostAdapter(
            mediaUrl = forum::mediaUrl,
            avatarUrl = forum::avatarUrl,
            onOpen = ::openPost,
            onLike = ::toggleLike,
            // Answering is taking part: a guest is offered sign-in first.
            onAnswers = { post -> if (requireSignIn()) openPost(post) },
        )
        adapter.guest = !forum.isSignedIn
        val layoutManager = LinearLayoutManager(requireContext())
        binding.rvPosts.layoutManager = layoutManager
        binding.rvPosts.adapter = adapter
        binding.rvPosts.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                val last = layoutManager.findLastVisibleItemPosition()
                if (dy > 0 && last >= adapter.itemCount - PREFETCH_DISTANCE) loadMore()
            }
        })
        binding.swipeRefresh.setOnRefreshListener { reload() }
        binding.chipFilters.setOnClickListener { showFilters() }
        binding.searchBar.setOnClickListener { startActivity(ForumSearchActivity.intent(requireContext())) }
        binding.btnMore.setOnClickListener { showMoreMenu() }
        binding.fabAsk.setOnClickListener {
            if (requireSignIn()) startActivity(ComposePostActivity.intent(requireContext()))
        }
        renderFilters()
        loadSpaces()
        reload()
    }

    override fun onResume() {
        super.onResume()
        if (_binding != null) adapter.guest = !forum.isSignedIn
        refreshActivity()
        refreshStaff()
        offerProfilePhoto()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    // ── profile photo ────────────────────────────────────────────────────

    /**
     * The profile photo stays on the device unless the member agrees: ask
     * once per photo, otherwise quietly bring the server in line with their
     * choice (a changed photo is re-uploaded, a removed one withdrawn).
     */
    private fun offerProfilePhoto() {
        if (photoPromptShowing) return
        viewLifecycleOwner.lifecycleScope.launch {
            if (!avatarSync.shouldAsk()) {
                onPhotoSynced(avatarSync.sync())
                return@launch
            }
            if (_binding == null || photoPromptShowing) return@launch
            photoPromptShowing = true
            MaterialAlertDialogBuilder(requireContext())
                .setTitle(R.string.community_photo_prompt_title)
                .setMessage(R.string.community_photo_prompt_message)
                .setPositiveButton(R.string.community_photo_prompt_show) { _, _ -> answerPhotoPrompt(true) }
                .setNegativeButton(R.string.community_photo_prompt_not_now) { _, _ -> answerPhotoPrompt(false) }
                .setOnCancelListener { answerPhotoPrompt(false) }
                .setOnDismissListener { photoPromptShowing = false }
                .show()
        }
    }

    private fun answerPhotoPrompt(show: Boolean) {
        viewLifecycleOwner.lifecycleScope.launch { onPhotoSynced(avatarSync.setShown(show)) }
    }

    private fun onPhotoSynced(outcome: CommunityAvatarSync.Outcome) {
        if (_binding == null) return
        when (outcome) {
            CommunityAvatarSync.Outcome.PUBLISHED, CommunityAvatarSync.Outcome.WITHDRAWN -> reload()
            CommunityAvatarSync.Outcome.REJECTED ->
                Toast.makeText(requireContext(), R.string.community_photo_rejected, Toast.LENGTH_LONG).show()
            CommunityAvatarSync.Outcome.NOTHING, CommunityAvatarSync.Outcome.FAILED -> Unit
        }
    }

    // ── feed ─────────────────────────────────────────────────────────────

    private fun reload() {
        nextCursor = null
        load(reset = true)
    }

    private fun loadMore() {
        if (nextCursor != null && loadJob?.isActive != true) load(reset = false)
    }

    private fun load(reset: Boolean) {
        loadJob?.cancel()
        val cursor = if (reset) null else nextCursor
        if (reset) binding.swipeRefresh.isRefreshing = true
        loadJob = viewLifecycleOwner.lifecycleScope.launch {
            forum.feed(tab, sort, space, cursor)
                .onSuccess { page ->
                    nextCursor = page.nextCursor
                    val items = if (reset) page.items else adapter.currentList + page.items
                    adapter.submitList(items)
                    showEmpty(if (items.isEmpty()) getString(emptyMessage()) else null)
                }
                .onFailure { error ->
                    if (reset) {
                        adapter.submitList(emptyList())
                        showEmpty(ForumUi.errorMessage(requireContext(), error))
                    }
                }
            _binding?.swipeRefresh?.isRefreshing = false
        }
    }

    private fun emptyMessage(): Int = when (tab) {
        TAB_NEEDS_HELP -> R.string.forum_empty_needs_help
        TAB_SOLVED -> R.string.forum_empty_solved
        TAB_MINE -> R.string.forum_empty_mine
        else -> R.string.forum_empty_all
    }

    private fun showEmpty(message: String?) {
        val b = _binding ?: return
        b.tvEmpty.visibility = if (message == null) View.GONE else View.VISIBLE
        b.tvEmpty.text = message
    }

    private fun toggleLike(post: ForumPost) {
        if (!requireSignIn() || post.author.isMe) return
        val next = !post.likedByMe
        adapter.updateLike(post.id, next, (post.likeCount + if (next) 1 else -1).coerceAtLeast(0))
        viewLifecycleOwner.lifecycleScope.launch {
            forum.likePost(post.id, next)
                .onSuccess { adapter.updateLike(post.id, it.liked, it.likeCount) }
                .onFailure { error ->
                    adapter.updateLike(post.id, post.likedByMe, post.likeCount)
                    toast(ForumUi.errorMessage(requireContext(), error))
                }
        }
    }

    // ── filters ─────────────────────────────────────────────────────────

    private fun loadSpaces() {
        viewLifecycleOwner.lifecycleScope.launch {
            forum.spaces().onSuccess {
                spaces = it
                adapter.spaces = it
                renderFilters()
            }
        }
    }

    /** The filter sheet. Choices apply together on "Show posts". */
    private fun showFilters() {
        val context = requireContext()
        if (spaces.isEmpty()) loadSpaces()
        val sheet = SheetForumFiltersBinding.inflate(layoutInflater)
        val dialog = BottomSheetDialog(context)
        dialog.setContentView(sheet.root)

        sheet.cgShow.check(SHOW_CHIPS.getValue(tab))
        val spaceChips = mutableMapOf<Int, String?>()
        fun addSpace(slug: String?, label: String, icon: Int) {
            val chip = layoutInflater.inflate(R.layout.item_forum_filter_chip, sheet.cgSpace, false) as Chip
            chip.id = View.generateViewId()
            chip.text = label
            chip.setChipIconResource(icon)
            sheet.cgSpace.addView(chip)
            spaceChips[chip.id] = slug
        }
        addSpace(null, getString(R.string.forum_all_spaces), ForumUi.spaceIcon("leaf"))
        spaces.forEach { addSpace(it.slug, ForumUi.spaceName(context, it.slug, spaces), ForumUi.spaceIcon(it.icon)) }
        sheet.cgSpace.check(spaceChips.entries.firstOrNull { it.value == space }?.key ?: spaceChips.keys.first())
        sheet.cgSort.check(SORT_CHIPS.getValue(sort))

        fun syncSortSection() {
            sheet.sortSection.visibility =
                if (sheet.cgShow.checkedChipId == R.id.chip_show_needs_help) View.GONE else View.VISIBLE
        }
        syncSortSection()
        sheet.cgShow.setOnCheckedStateChangeListener { _, _ -> syncSortSection() }
        sheet.btnReset.setOnClickListener {
            sheet.cgShow.check(R.id.chip_show_all)
            sheet.cgSpace.check(spaceChips.keys.first())
            sheet.cgSort.check(R.id.chip_sort_hot)
        }
        sheet.btnApply.setOnClickListener {
            val nextTab = SHOW_CHIPS.entries.first { it.value == sheet.cgShow.checkedChipId }.key
            if (nextTab == TAB_MINE && !requireSignIn()) {
                dialog.dismiss()
                return@setOnClickListener
            }
            applyFilters(
                nextTab = nextTab,
                nextSpace = spaceChips[sheet.cgSpace.checkedChipId],
                nextSort = SORT_CHIPS.entries.first { it.value == sheet.cgSort.checkedChipId }.key,
            )
            dialog.dismiss()
        }
        dialog.show()
    }

    private fun applyFilters(nextTab: String = tab, nextSpace: String? = space, nextSort: String = sort) {
        val changed = nextTab != tab || nextSpace != space || nextSort != sort
        tab = nextTab
        space = nextSpace
        sort = nextSort
        renderFilters()
        if (changed) reload()
    }

    /** Only the filters that differ from the default, each with a remove icon. */
    private fun renderFilters() {
        val b = _binding ?: return
        val context = requireContext()
        val group = b.cgActiveFilters
        group.removeAllViews()
        fun add(label: String, icon: Int?, onRemove: () -> Unit) {
            val chip = layoutInflater.inflate(R.layout.item_forum_active_filter, group, false) as Chip
            chip.text = label
            if (icon != null) chip.setChipIconResource(icon) else chip.isChipIconVisible = false
            chip.closeIconContentDescription = getString(R.string.forum_filter_remove, label)
            chip.setOnCloseIconClickListener { onRemove() }
            chip.setOnClickListener { showFilters() }
            group.addView(chip)
        }
        if (tab != TAB_ALL) add(getString(TAB_LABELS.getValue(tab)), null) { applyFilters(nextTab = TAB_ALL) }
        space?.let { slug ->
            val icon = spaces.firstOrNull { it.slug == slug }?.icon ?: "leaf"
            add(ForumUi.spaceName(context, slug, spaces), ForumUi.spaceIcon(icon)) { applyFilters(nextSpace = null) }
        }
        if (sort != SORT_HOT && tab != TAB_NEEDS_HELP) {
            add(getString(SORT_LABELS.getValue(sort)), null) { applyFilters(nextSort = SORT_HOT) }
        }
        val count = group.childCount
        b.activeFiltersScroll.visibility = if (count == 0) View.GONE else View.VISIBLE
        b.chipFilters.text = if (count == 0) {
            getString(R.string.forum_filters)
        } else {
            getString(R.string.forum_filters_active, count)
        }
    }

    // ── activity, staff, menu ───────────────────────────────────────────

    /** Pulls Community events into the notification centre (Home's bell) and
     *  updates the Главная tab's dot; a new moderator decision pops up here. */
    private fun refreshActivity() {
        if (!forum.isSignedIn) return
        viewLifecycleOwner.lifecycleScope.launch {
            forumSync.sync()
            (activity as? MainActivity)?.refreshHomeBadge()
            showNewDecision()
        }
    }

    /**
     * A moderator's decision the user has not been shown yet pops up once, over
     * Community; after that it lives only in the notifications. Decisions older
     * than [DECISION_POPUP_MAX_AGE_S] are not worth interrupting for.
     */
    private fun showNewDecision() {
        if (!isResumed || isHidden) return
        val prefs = requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val newest = forumSync.notices.maxByOrNull { it.createdAt } ?: return
        if (newest.createdAt <= prefs.getLong(KEY_DECISION_SHOWN, 0L)) return
        prefs.edit().putLong(KEY_DECISION_SHOWN, newest.createdAt).apply()
        if (System.currentTimeMillis() / MILLIS - newest.createdAt > DECISION_POPUP_MAX_AGE_S) return
        val context = requireContext()
        val message = listOfNotNull(
            ForumUi.modNoticeHeading(context, newest),
            newest.postTitle?.takeIf { it.isNotBlank() }?.let { getString(R.string.forum_quoted, it) },
        ).joinToString("\n")
        MaterialAlertDialogBuilder(context)
            .setIcon(R.drawable.ic_shield_check)
            .setTitle(R.string.forum_decision_title)
            .setMessage(message)
            .setNegativeButton(R.string.forum_close, null)
            .setPositiveButton(R.string.forum_decision_details) { _, _ ->
                startActivity(ModerationNoticesActivity.intent(context))
            }
            .show()
    }

    private fun refreshStaff() {
        if (!forum.isSignedIn) {
            staff = null
            return
        }
        viewLifecycleOwner.lifecycleScope.launch {
            forum.staffMe().onSuccess { staff = it }
        }
    }

    private fun showMoreMenu() {
        val context = requireContext()
        val items = buildList {
            add(
                ForumMenu.Item(R.string.forum_my_achievements, R.drawable.ic_trophy, ForumMenu.Tone.AMBER) {
                    if (requireSignIn()) startActivity(HelperProfileActivity.intentForMe(context))
                },
            )
            if (staff?.isStaff == true) {
                add(
                    ForumMenu.Item(R.string.forum_moderation_tools, R.drawable.ic_flag, ForumMenu.Tone.RED) {
                        if (requireSignIn()) startActivity(ModerationActivity.intent(context))
                    },
                )
            }
            add(
                ForumMenu.Item(R.string.community_map_title, R.drawable.ic_world_map, ForumMenu.Tone.BLUE) {
                    startActivity(Intent(context, CommunityMapActivity::class.java))
                },
            )
        }
        ForumMenu(binding.btnMore).show(items)
    }

    private fun requireSignIn(): Boolean = quickSignIn.require()

    private fun openPost(post: ForumPost) {
        startActivity(PostDetailActivity.intent(requireContext(), post.id))
    }

    /** Signed in from the sheet: likes and answers unlock, personal data loads. */
    private fun onSignedIn() {
        if (_binding == null) return
        adapter.guest = false
        refreshStaff()
        reload()
    }

    private fun toast(message: String) {
        Toast.makeText(requireContext(), message, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        const val TAB_ALL = "all"
        const val TAB_NEEDS_HELP = "needs_help"
        const val TAB_SOLVED = "solved"
        const val TAB_MINE = "mine"
        const val SORT_HOT = "hot"
        const val SORT_NEW = "new"
        const val SORT_TOP = "top"
        const val PREFETCH_DISTANCE = 3
        const val PREFS = "forum_prefs"
        const val KEY_DECISION_SHOWN = "decision_shown_at"
        const val MILLIS = 1000L
        const val DECISION_POPUP_MAX_AGE_S = 7 * 24 * 60 * 60L
        val SHOW_CHIPS = mapOf(
            TAB_ALL to R.id.chip_show_all,
            TAB_NEEDS_HELP to R.id.chip_show_needs_help,
            TAB_SOLVED to R.id.chip_show_solved,
            TAB_MINE to R.id.chip_show_mine,
        )
        val SORT_CHIPS = mapOf(
            SORT_HOT to R.id.chip_sort_hot,
            SORT_NEW to R.id.chip_sort_new,
            SORT_TOP to R.id.chip_sort_top,
        )
        val TAB_LABELS = mapOf(
            TAB_ALL to R.string.forum_tab_all,
            TAB_NEEDS_HELP to R.string.forum_tab_needs_help,
            TAB_SOLVED to R.string.forum_tab_solved,
            TAB_MINE to R.string.forum_tab_mine,
        )
        val SORT_LABELS = mapOf(
            SORT_HOT to R.string.forum_sort_hot,
            SORT_NEW to R.string.forum_sort_new,
            SORT_TOP to R.string.forum_sort_top,
        )
    }
}
