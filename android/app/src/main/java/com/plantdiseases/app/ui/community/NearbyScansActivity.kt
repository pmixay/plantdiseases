package com.plantdiseases.app.ui.community

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityNearbyScansBinding
import com.plantdiseases.app.databinding.SheetNearbySortBinding
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingContext
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingCoordinator
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStateStore
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.ResolvedRegion
import com.plantdiseases.app.util.enableWhileOnline
import com.plantdiseases.app.util.onReconnected
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Scrolling-forum community feed (Community v2). A single column, cursor-
 * paged from the server: the first page loads [FEED_PAGE_SIZE] posts, and
 * scrolling near the end fetches the next (older) page transparently. Each
 * post's photo or comments row opens [CommunityOpinionSheet] — the vote
 * graph + comment thread + the user's own vote; its like control toggles
 * in place via [toggleLike], never opening the sheet.
 *
 * A toolbar sort action switches between the server's freshness-led
 * recommendation order and strict newest-first order.
 */
@AndroidEntryPoint
class NearbyScansActivity : AppCompatActivity() {

    private lateinit var binding: ActivityNearbyScansBinding
    private lateinit var adapter: NearbyScansAdapter

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var authRepository: AuthRepository

    @Inject
    lateinit var connectivity: ConnectivityObserver

    @Inject
    lateinit var onboardingState: OnboardingStateStore
    private lateinit var onboardingCoordinator: OnboardingCoordinator
    private val isRu by lazy { LocaleHelper.isRussian(this) }

    // Full feed in the order returned by the server. Every sort change starts
    // a new cursor session so recommendation and recent cursors never mix.
    private var allItems: List<CommunityScanItem> = emptyList()
    private var ranking = RANKING_RECOMMENDED
    private var sortMenuItem: MenuItem? = null
    private var disableMenuItem: MenuItem? = null
    private var mapMenuItem: MenuItem? = null

    // Server-side scope filter: "nearby" (~30 km radius), "country", or
    // "worldwide" (no geo filter — needs no location).
    private var scope = "nearby"

    // Stable composite cursor returned by the selected ranking endpoint.
    private var nextCursor: String? = null
    private var hasMorePages = true
    private var isLoadingPage = false
    private var currentRegion: ResolvedRegion? = null

    // Bumped by every (re)load. A page fetch captures it and drops its result
    // if it no longer matches — otherwise a loadMore() still in flight when the
    // user pulls to refresh writes its cursor (and its rows) over the fresh
    // feed, leaving its cursor pointing past data the list no longer holds.
    private var loadGeneration = 0

    // Feed-scroll impressions already reported this screen session, so a
    // post scrolled past twice doesn't get reported twice from the client
    // side too (the server also dedupes, this just avoids the round trip).
    private val reportedImpressions = mutableSetOf<String>()
    private val likeWritesInFlight = mutableSetOf<String>()

    // Public browsing may request coarse location for the Nearby scope. This
    // does not enable publication or notifications.
    private val communityPermissionLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission(),
    ) { _ ->
        load(isRefresh = false)
    }

    private fun requestCommunityLocation() {
        communityPermissionLauncher.launch(android.Manifest.permission.ACCESS_COARSE_LOCATION)
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityNearbyScansBinding.inflate(layoutInflater)
        setContentView(binding.root)
        onboardingCoordinator = OnboardingCoordinator(this, onboardingState)
        // Activate only after a visible terminal state is rendered. Starting
        // during loading leaves the required feed anchor hidden and causes the
        // coordinator to reschedule forever on empty/error responses.
        onboardingCoordinator.setContext(null)
        onboardingCoordinator.registerAnchor(OnboardingAnchorKey.COMMUNITY_SCOPE, binding.cgScope)
        onboardingCoordinator.registerAnchor(OnboardingAnchorKey.COMMUNITY_FEED, binding.rvNearby)

        val baseListPadding = resources.getDimensionPixelSize(R.dimen.space_2xl)
        ViewCompat.setOnApplyWindowInsetsListener(binding.appBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(top = bars.top)
            binding.rvNearby.updatePadding(bottom = bars.bottom + baseListPadding)
            insets
        }

        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.offlineNotice.observe(this, connectivity.isOnline)
        // Location-scoped browsing needs the app API online.
        enableWhileOnline(connectivity.isOnline, binding.btnEnableCommunity)
        onReconnected(connectivity.isOnline) { load(isRefresh = false) }
        setupToolbarMenu()
        supportFragmentManager.setFragmentResultListener(
            CommunityOpinionSheet.CONTENT_BLOCKED,
            this,
        ) { _, _ -> load(isRefresh = false) }

        // Nearby (~30 km) / Country / Worldwide scope — reload on change.
        binding.cgScope.setOnCheckedStateChangeListener { _, checked ->
            val next = when (checked.firstOrNull()) {
                R.id.chip_scope_country -> "country"
                R.id.chip_scope_worldwide -> "worldwide"
                else -> "nearby"
            }
            if (next != scope) {
                scope = next
                load(isRefresh = false)
            }
        }
        binding.btnEnableCommunity.setOnClickListener { requestCommunityLocation() }

        adapter = NearbyScansAdapter(
            onOpen = { item ->
                // Opening someone else's scan counts as a view (deduped per
                // signed-in viewer server-side).
                lifecycleScope.launch {
                    // Guests browse without creating a view row; the repository
                    // sends this only when an account access token is available.
                    scanRepository.recordCommunityView(item.scanId)
                }
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    item,
                    DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
                )
            },
            onAuthor = { item -> openCommunityProfile(item) },
            onLike = { item -> toggleLike(item) },
        )
        val layoutManager = LinearLayoutManager(this)
        binding.rvNearby.layoutManager = layoutManager
        binding.rvNearby.adapter = adapter
        // Cursor-page the next (older) batch once the user scrolls near the
        // end of what's loaded — the classic "5 rows from the bottom" trigger.
        binding.rvNearby.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                if (dy <= 0 || isLoadingPage || !hasMorePages) return
                val lastVisible = layoutManager.findLastVisibleItemPosition()
                if (lastVisible >= adapter.itemCount - 5) loadMore()
            }

            // Impressions ("scrolled past", not necessarily opened) report
            // once scrolling actually settles — not per frame — so a fast
            // flick through the feed doesn't fire a request per pixel.
            override fun onScrollStateChanged(recyclerView: RecyclerView, newState: Int) {
                if (newState != RecyclerView.SCROLL_STATE_IDLE) return
                reportVisibleImpressions(layoutManager)
            }
        })

        binding.swipeRefresh.setColorSchemeResources(R.color.primary)
        binding.swipeRefresh.setOnRefreshListener { load(isRefresh = true) }

        load(isRefresh = false)
    }

    /** Optimistic like toggle: flips the row immediately, then reconciles
     *  with the server's authoritative count/state (or rolls back on
     *  failure). [item] is whatever the adapter last bound, so its
     *  likeCount/likedByMe are the pre-tap truth to roll back to. */
    private fun toggleLike(item: CommunityScanItem) {
        if (authRepository.state.value !is AuthState.Authenticated) {
            Toast.makeText(this, R.string.community_sign_in_required, Toast.LENGTH_SHORT).show()
            startActivity(com.plantdiseases.app.ui.account.AccountActivity.intent(this))
            return
        }
        if (!likeWritesInFlight.add(item.scanId)) return
        val next = !item.likedByMe
        val optimisticCount = (item.likeCount + if (next) 1 else -1).coerceAtLeast(0)
        adapter.updateLike(item.scanId, optimisticCount, next)
        lifecycleScope.launch {
            scanRepository.setCommunityLike(item.scanId, next)
                .onSuccess { adapter.updateLike(item.scanId, it.count, it.liked) }
                .onFailure { adapter.updateLike(item.scanId, item.likeCount, item.likedByMe) }
            likeWritesInFlight.remove(item.scanId)
        }
    }

    private fun openCommunityProfile(item: CommunityScanItem) {
        val token = item.authorToken?.takeIf { it.startsWith("ca") && it.length == 32 }
        if (token == null) {
            Toast.makeText(this, R.string.community_profile_unavailable, Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(CommunityProfileActivity.intent(this, token))
    }

    /** Batch-report every currently visible post not already reported this
     *  session, capped to [FEED_PAGE_SIZE] * 2 or so — the actual cap that
     *  matters is the server's 50 per call, applied in
     *  [ScanRepository.recordCommunityViews]. */
    private fun reportVisibleImpressions(layoutManager: LinearLayoutManager) {
        val first = layoutManager.findFirstVisibleItemPosition()
        val last = layoutManager.findLastVisibleItemPosition()
        if (first < 0 || last < 0) return
        val newIds = (first..last).mapNotNull { adapter.itemAt(it)?.scanId }
            .filter { it.isNotBlank() && it !in reportedImpressions }
        if (newIds.isEmpty()) return
        reportedImpressions.addAll(newIds)
        lifecycleScope.launch {
            scanRepository.recordCommunityViews(newIds)
        }
    }

    private fun setupToolbarMenu() {
        binding.toolbar.inflateMenu(R.menu.nearby_scans_menu)
        sortMenuItem = binding.toolbar.menu.findItem(R.id.action_sort)
        disableMenuItem = binding.toolbar.menu.findItem(R.id.action_disable_community)
        mapMenuItem = binding.toolbar.menu.findItem(R.id.action_map)
        // Hidden until a feed loads — sorting an empty screen is a no-op.
        sortMenuItem?.isVisible = false
        // "Leave community" only makes sense once the user is in it.
        disableMenuItem?.isVisible = CommunityPrefs.uploadsEnabled(this)
        mapMenuItem?.isVisible = CommunityPrefs.uploadsEnabled(this)
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_map -> {
                    startActivity(
                        android.content.Intent(this, CommunityMapActivity::class.java),
                    )
                    true
                }
                R.id.action_sort -> {
                    showSortSheet()
                    true
                }
                R.id.action_community_safety -> {
                    showCommunitySafety()
                    true
                }
                R.id.action_disable_community -> {
                    confirmLeaveCommunity()
                    true
                }
                else -> false
            }
        }
    }

    private fun showCommunitySafety() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.community_rules_title)
            .setMessage(R.string.community_rules_body)
            .setNegativeButton(R.string.close, null)
            .setNeutralButton(R.string.community_manage_blocks) { _, _ ->
                showBlockedAuthors()
            }
            .setPositiveButton(R.string.community_contact_moderation) { _, _ ->
                runCatching {
                    startActivity(
                        Intent(
                            Intent.ACTION_VIEW,
                            Uri.parse(getString(R.string.community_moderation_url)),
                        ),
                    )
                }.onFailure {
                    Toast.makeText(this, R.string.community_safety_error, Toast.LENGTH_SHORT).show()
                }
            }
            .show()
    }

    private fun showBlockedAuthors() {
        if (authRepository.state.value !is AuthState.Authenticated) {
            Toast.makeText(this, R.string.community_sign_in_required, Toast.LENGTH_SHORT).show()
            startActivity(com.plantdiseases.app.ui.account.AccountActivity.intent(this))
            return
        }
        lifecycleScope.launch {
            scanRepository.communityBlocks()
                .onSuccess { blocks ->
                    if (blocks.isEmpty()) {
                        Toast.makeText(
                            this@NearbyScansActivity,
                            R.string.community_blocks_empty,
                            Toast.LENGTH_SHORT,
                        ).show()
                        return@onSuccess
                    }
                    val labels = blocks.map {
                        it.nickname?.takeIf(String::isNotBlank)
                            ?: getString(R.string.community_unknown_author)
                    }.toTypedArray()
                    MaterialAlertDialogBuilder(this@NearbyScansActivity)
                        .setTitle(R.string.community_manage_blocks)
                        .setItems(labels) { _, index ->
                            confirmUnblock(blocks[index].blockId, labels[index])
                        }
                        .setNegativeButton(R.string.close, null)
                        .show()
                }
                .onFailure {
                    Toast.makeText(
                        this@NearbyScansActivity,
                        R.string.community_safety_error,
                        Toast.LENGTH_SHORT,
                    ).show()
                }
        }
    }

    private fun confirmUnblock(blockId: String, label: String) {
        MaterialAlertDialogBuilder(this)
            .setTitle(getString(R.string.community_unblock_title, label))
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_unblock_confirm) { _, _ ->
                lifecycleScope.launch {
                    scanRepository.unblockCommunityAuthor(blockId)
                        .onSuccess {
                            Toast.makeText(
                                this@NearbyScansActivity,
                                R.string.community_author_unblocked,
                                Toast.LENGTH_SHORT,
                            ).show()
                            load(isRefresh = false)
                        }
                        .onFailure {
                            Toast.makeText(
                                this@NearbyScansActivity,
                                R.string.community_safety_error,
                                Toast.LENGTH_SHORT,
                            ).show()
                        }
                }
            }
            .show()
    }

    /** Turn community off from inside the community screen (the on/off control
     *  no longer lives in Profile). Stops sharing + opinion delivery; already
     *  shared scans persist until the user deletes them from Profile. */
    private fun confirmLeaveCommunity() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.community_disable_title)
            .setMessage(R.string.community_disable_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_disable_confirm) { _, _ ->
                CommunityPrefs.setUploadsEnabled(this, false) // also stops opinions
                com.plantdiseases.app.util.OpinionPrefs.setNotificationsEnabled(this, false)
                lifecycleScope.launch {
                    com.plantdiseases.app.di.appEntryPoint(this@NearbyScansActivity)
                        .authRepository().disablePush()
                }
                androidx.work.WorkManager.getInstance(this).cancelUniqueWork("opinion_check")
                androidx.work.WorkManager.getInstance(this).cancelUniqueWork("opinion_check_now")
                disableMenuItem?.isVisible = false
                load(isRefresh = false)
            }
            .show()
    }

    private fun load(isRefresh: Boolean) {
        // The feed is live-only — nothing about it is cached. Offline, say so
        // instead of spinning through a request that can only time out (every
        // scope change and pull-to-refresh would pay for one). The observer
        // re-runs this the moment the server is reachable again.
        if (!connectivity.lastKnownOnline) {
            binding.swipeRefresh.isRefreshing = false
            showState(getString(R.string.no_network))
            return
        }
        disableMenuItem?.isVisible = CommunityPrefs.uploadsEnabled(this)
        mapMenuItem?.isVisible = true
        if (!isRefresh) showLoading()
        // Fresh (re)load starts the cursor over. A pull-to-refresh keeps the
        // populated list on screen while the location fix resolves, so a
        // loadMore() can still be in flight (or fire) meanwhile — the
        // generation bump makes both of them no-ops against this load.
        nextCursor = null
        hasMorePages = true
        val generation = ++loadGeneration
        lifecycleScope.launch {
            // Refresh location on every (re)load so "nearby" reflects where the
            // user actually is right now. Worldwide needs no anchor, so we skip
            // the location read entirely — the feed then works even when
            // coarse-location was never granted. The resolved region is reused
            // by loadMore() so every page of one scroll session shares the same
            // geo anchor instead of drifting as the device moves mid-scroll.
            currentRegion = if (scope == "worldwide") {
                null
            } else {
                runCatching {
                    RegionResolver.resolveRegion(this@NearbyScansActivity)
                }.getOrNull()
            }
            val result = fetchPage(cursor = null)
            // Guard first: a superseded load must not clear the spinner that
            // belongs to the load which replaced it — that one clears its own.
            if (generation != loadGeneration) return@launch
            binding.swipeRefresh.isRefreshing = false
            result.onSuccess { response ->
                nextCursor = response.nextCursor
                hasMorePages = response.nextCursor != null
                val others = excludeOwnScans(response.items)
                when {
                    others.isNotEmpty() -> showFeed(others)
                    // The server couldn't geo-scope the feed (no coords + no
                    // country) — usually community is on but coarse-location was
                    // never granted / has no fix yet. Prompt to enable location
                    // rather than claim there's nothing nearby.
                    response.locationRequired -> showLocationNeeded()
                    else -> showState(
                        getString(R.string.similar_scans_empty),
                        enableOnboarding = true,
                    )
                }
            }.onFailure {
                showState(getString(R.string.community_opinion_error))
            }
        }
    }

    /** Fetch and append the next (older) page once the user scrolls near the
     *  end of what's loaded. Silent — no separate loading footer, matching
     *  the plan's "reuse the existing states" call; a failed page load just
     *  leaves the feed as it is and lets the next scroll retry. */
    private fun loadMore() {
        // No cursor means "page 1" — either nothing has loaded yet or a
        // (re)load just reset it and is still awaiting its location fix. Either
        // way, re-requesting page 1 here would append the rows the list already
        // shows (every post twice) and leave the cursor wherever the last
        // responder wrote it. hasMorePages covers the exhausted case.
        val cursor = nextCursor ?: return
        isLoadingPage = true
        val generation = loadGeneration
        lifecycleScope.launch {
            val result = fetchPage(cursor = cursor)
            isLoadingPage = false
            if (generation != loadGeneration) return@launch
            result.onSuccess { response ->
                nextCursor = response.nextCursor
                hasMorePages = response.nextCursor != null
                val incoming = excludeOwnScans(response.items)
                val known = allItems.asSequence().map { it.scanId }.toHashSet()
                allItems = allItems + incoming.filter { known.add(it.scanId) }
                renderFeed()
            }
        }
    }

    private suspend fun fetchPage(cursor: String?) =
        scanRepository.communityNearby(
            country = currentRegion?.country,
            region = currentRegion?.locality,
            lat = currentRegion?.lat,
            lon = currentRegion?.lon,
            // No disease filter — the feed surfaces the whole scope.
            disease = null,
            limit = FEED_PAGE_SIZE,
            scope = scope,
            ranking = ranking,
            cursor = cursor,
        )

    // Belt-and-braces: also drop the user's own scans client-side. The server
    // excludes the signed-in actor, while this catches local rows created
    // before account identity was introduced.
    private suspend fun excludeOwnScans(items: List<CommunityScanItem>): List<CommunityScanItem> {
        val mine = scanRepository.localClientScanIds()
        return items.filter { it.scanId !in mine }
    }

    /** Opt-in prompt shown until the user enables community. */
    private fun showEnablePrompt() {
        binding.swipeRefresh.isRefreshing = false
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.tvState.text = getString(R.string.community_enable_blurb)
        binding.btnEnableCommunity.visibility = View.VISIBLE
        binding.cgScope.visibility = View.GONE
        sortMenuItem?.isVisible = false
        disableMenuItem?.isVisible = false
        mapMenuItem?.isVisible = false
        onboardingCoordinator.setContext(null)
        binding.rvNearby.visibility = View.GONE
    }

    /** Empty-state shown when the feed can't be geo-scoped because we have no
     *  location. Offers the same Enable button so a tap (re)requests the coarse
     *  location permission and reloads. */
    private fun showLocationNeeded() {
        binding.swipeRefresh.isRefreshing = false
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.tvState.text = getString(R.string.nearby_location_needed)
        binding.btnEnableCommunity.visibility = View.VISIBLE
        binding.btnEnableCommunity.setText(R.string.community_location_cta)
        binding.cgScope.visibility = View.VISIBLE
        sortMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
        onboardingCoordinator.setContext(OnboardingContext.COMMUNITY)
    }

    private fun showLoading() {
        onboardingCoordinator.setContext(null)
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.VISIBLE
        binding.ivStateIcon.visibility = View.GONE
        binding.tvState.text = getString(R.string.nearby_scans_loading)
        binding.btnEnableCommunity.visibility = View.GONE
        binding.cgScope.visibility = View.VISIBLE
        sortMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showState(message: String, enableOnboarding: Boolean = false) {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.tvState.text = message
        binding.btnEnableCommunity.visibility = View.GONE
        binding.cgScope.visibility = View.VISIBLE
        sortMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
        onboardingCoordinator.setContext(
            OnboardingContext.COMMUNITY.takeIf { enableOnboarding },
        )
    }

    private fun showFeed(items: List<CommunityScanItem>) {
        allItems = items
        binding.btnEnableCommunity.visibility = View.GONE
        binding.cgScope.visibility = View.VISIBLE
        sortMenuItem?.isVisible = true
        renderFeed()
        onboardingCoordinator.setContext(OnboardingContext.COMMUNITY)
    }

    /** Render the server-ordered feed without client-side reordering. */
    private fun renderFeed() {
        binding.stateLayout.visibility = View.GONE
        binding.rvNearby.visibility = View.VISIBLE
        adapter.submit(allItems)
        binding.rvNearby.post {
            (binding.rvNearby.layoutManager as? LinearLayoutManager)
                ?.let(::reportVisibleImpressions)
        }
    }

    /**
     * Choose the server-side feed order. Applying a new choice reloads page
     * one because each ranking has its own opaque cursor contract.
     */
    private fun showSortSheet() {
        if (allItems.isEmpty()) return
        val sheet = SheetNearbySortBinding.inflate(layoutInflater)
        val dialog = BottomSheetDialog(this)
        dialog.setContentView(sheet.root)

        sheet.cgSort.check(
            if (ranking == RANKING_RECENT) {
                R.id.chip_sort_newest
            } else {
                R.id.chip_sort_recommended
            },
        )

        sheet.btnApply.setOnClickListener {
            val nextRanking = if (sheet.cgSort.checkedChipId == R.id.chip_sort_newest) {
                RANKING_RECENT
            } else {
                RANKING_RECOMMENDED
            }
            dialog.dismiss()
            if (nextRanking != ranking) {
                ranking = nextRanking
                load(isRefresh = false)
            }
        }
        dialog.show()
    }

    private companion object {
        // First page + every subsequent page. Small enough that a scroll-
        // triggered fetch feels instant; the server clamps to 50 regardless.
        const val FEED_PAGE_SIZE = 20
        const val RANKING_RECOMMENDED = "recommended"
        const val RANKING_RECENT = "recent"
    }
}
