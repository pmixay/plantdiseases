package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.PopupMenu
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumActivityItem
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.model.StaffMe
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.databinding.FragmentCommunityBinding
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.ui.community.NearbyScansActivity
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The Community tab: the forum feed with For you / Needs help / Solved / Mine
 * tabs, a space filter, sort, search, recent activity and the Ask button.
 * The older map and nearby-scans screens stay one tap away (Nearby).
 */
@AndroidEntryPoint
class CommunityFragment : Fragment() {

    @Inject
    lateinit var forum: ForumRepository

    private var _binding: FragmentCommunityBinding? = null
    private val binding get() = _binding!!

    private lateinit var adapter: ForumPostAdapter
    private var tab = TAB_ALL
    private var sort = SORT_HOT
    private var space: String? = null
    private var spaces: List<ForumSpace> = emptyList()
    private var nextCursor: String? = null
    private var loadJob: Job? = null
    private var staff: StaffMe? = null
    private var activityItems: List<ForumActivityItem> = emptyList()

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentCommunityBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        adapter = ForumPostAdapter(
            mediaUrl = forum::mediaUrl,
            onOpen = { post -> startActivity(PostDetailActivity.intent(requireContext(), post.id)) },
            onLike = ::toggleLike,
        )
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
        binding.chipTabs.setOnCheckedStateChangeListener { _, checked -> onTabChanged(checked.firstOrNull()) }
        binding.chipSpace.setOnClickListener { pickSpace() }
        binding.chipNearby.setOnClickListener {
            startActivity(Intent(requireContext(), NearbyScansActivity::class.java))
        }
        binding.tvSort.setOnClickListener { pickSort() }
        binding.btnSearch.setOnClickListener { startActivity(ForumSearchActivity.intent(requireContext())) }
        binding.btnActivity.setOnClickListener { showActivity() }
        binding.btnMore.setOnClickListener { showMoreMenu() }
        binding.fabAsk.setOnClickListener {
            if (requireSignIn()) startActivity(ComposePostActivity.intent(requireContext()))
        }
        renderSort()
        loadSpaces()
        reload()
    }

    override fun onResume() {
        super.onResume()
        refreshActivity()
        refreshStaff()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    // ── feed ─────────────────────────────────────────────────────────────

    private fun onTabChanged(chipId: Int?) {
        val next = when (chipId) {
            R.id.chip_needs_help -> TAB_NEEDS_HELP
            R.id.chip_solved -> TAB_SOLVED
            R.id.chip_mine -> TAB_MINE
            else -> TAB_ALL
        }
        if (next == TAB_MINE && !requireSignIn()) {
            binding.chipAll.isChecked = true
            return
        }
        if (next == tab) return
        tab = next
        binding.tvSort.visibility = if (tab == TAB_NEEDS_HELP) View.GONE else View.VISIBLE
        reload()
    }

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

    // ── spaces and sort ─────────────────────────────────────────────────

    private fun loadSpaces() {
        viewLifecycleOwner.lifecycleScope.launch {
            forum.spaces().onSuccess {
                spaces = it
                adapter.spaces = it
                renderSpaceChip()
            }
        }
    }

    private fun pickSpace() {
        if (spaces.isEmpty()) {
            loadSpaces()
            return
        }
        val names = listOf(getString(R.string.forum_all_spaces)) +
            spaces.map { ForumUi.spaceName(requireContext(), it.slug, spaces) }
        val selected = spaces.indexOfFirst { it.slug == space } + 1
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.forum_spaces)
            .setSingleChoiceItems(names.toTypedArray(), selected) { dialog, which ->
                space = if (which == 0) null else spaces[which - 1].slug
                renderSpaceChip()
                dialog.dismiss()
                reload()
            }
            .show()
    }

    private fun renderSpaceChip() {
        val b = _binding ?: return
        val current = spaces.firstOrNull { it.slug == space }
        b.chipSpace.text = if (current == null) {
            getString(R.string.forum_all_spaces)
        } else {
            ForumUi.spaceName(requireContext(), current.slug, spaces)
        }
        b.chipSpace.setChipIconResource(ForumUi.spaceIcon(current?.icon ?: "leaf"))
    }

    private fun pickSort() {
        val popup = PopupMenu(requireContext(), binding.tvSort)
        popup.menu.add(0, 0, 0, R.string.forum_sort_hot)
        popup.menu.add(0, 1, 1, R.string.forum_sort_new)
        popup.menu.add(0, 2, 2, R.string.forum_sort_top)
        popup.setOnMenuItemClickListener { item ->
            sort = when (item.itemId) {
                1 -> SORT_NEW
                2 -> SORT_TOP
                else -> SORT_HOT
            }
            renderSort()
            reload()
            true
        }
        popup.show()
    }

    private fun renderSort() {
        binding.tvSort.setText(
            when (sort) {
                SORT_NEW -> R.string.forum_sort_new
                SORT_TOP -> R.string.forum_sort_top
                else -> R.string.forum_sort_hot
            },
        )
    }

    // ── activity, staff, menu ───────────────────────────────────────────

    private fun refreshActivity() {
        if (!forum.isSignedIn) {
            _binding?.activityDot?.visibility = View.GONE
            (activity as? MainActivity)?.setCommunityBadge(0)
            return
        }
        viewLifecycleOwner.lifecycleScope.launch {
            forum.activity(since = 0).onSuccess { response ->
                activityItems = response.items
                val unseen = response.items.count { it.createdAt > lastSeenActivity() }
                _binding?.activityDot?.visibility = if (unseen > 0) View.VISIBLE else View.GONE
                (activity as? MainActivity)?.setCommunityBadge(unseen)
            }
        }
    }

    private fun showActivity() {
        if (!requireSignIn()) return
        val context = requireContext()
        if (activityItems.isEmpty()) {
            toast(getString(R.string.forum_activity_empty))
            return
        }
        val labels = activityItems.map { item ->
            val title = item.postTitle?.takeIf { it.isNotBlank() } ?: getString(R.string.forum_kind_scan)
            when (item.type) {
                "answer_accepted" -> getString(R.string.forum_activity_accepted, title)
                "reply_to_answer" -> getString(R.string.forum_activity_reply, item.nickname, title)
                else -> getString(R.string.forum_activity_answer, item.nickname, title)
            }
        }
        markActivitySeen()
        MaterialAlertDialogBuilder(context)
            .setTitle(R.string.forum_activity)
            .setItems(labels.toTypedArray()) { _, which ->
                startActivity(PostDetailActivity.intent(context, activityItems[which].postId))
            }
            .setPositiveButton(R.string.forum_close, null)
            .show()
    }

    private fun lastSeenActivity(): Long =
        requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_SEEN, 0L)

    private fun markActivitySeen() {
        val newest = activityItems.maxOfOrNull { it.createdAt } ?: return
        requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(KEY_SEEN, newest).apply()
        _binding?.activityDot?.visibility = View.GONE
        (activity as? MainActivity)?.setCommunityBadge(0)
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
        val popup = PopupMenu(requireContext(), binding.btnMore)
        popup.menu.add(0, MENU_ACHIEVEMENTS, 0, R.string.forum_my_achievements)
        popup.menu.add(0, MENU_NOTICES, 1, R.string.forum_moderation_notices)
        if (staff?.isStaff == true) popup.menu.add(0, MENU_MODERATION, 2, R.string.forum_moderation_tools)
        popup.setOnMenuItemClickListener { item ->
            if (!requireSignIn()) return@setOnMenuItemClickListener true
            val context = requireContext()
            when (item.itemId) {
                MENU_ACHIEVEMENTS -> startActivity(HelperProfileActivity.intentForMe(context))
                MENU_NOTICES -> startActivity(ModerationNoticesActivity.intent(context))
                MENU_MODERATION -> startActivity(ModerationActivity.intent(context))
            }
            true
        }
        popup.show()
    }

    private fun requireSignIn(): Boolean {
        if (forum.isSignedIn) return true
        toast(getString(R.string.community_sign_in_required))
        startActivity(AccountActivity.intent(requireContext()))
        return false
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
        const val KEY_SEEN = "activity_seen_at"
        const val MENU_ACHIEVEMENTS = 1
        const val MENU_NOTICES = 2
        const val MENU_MODERATION = 3
    }
}
