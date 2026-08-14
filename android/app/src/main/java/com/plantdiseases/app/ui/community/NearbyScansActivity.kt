package com.plantdiseases.app.ui.community

import android.content.Context
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.chip.Chip
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityNearbyScansBinding
import com.plantdiseases.app.databinding.SheetNearbyFilterBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.ResolvedRegion
import com.plantdiseases.app.util.TelemetryIds
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
 * A toolbar filter action opens a bottom-sheet overlay to sort the feed
 * by date (newest / oldest) and narrow it to a single disease type — both
 * apply over whatever pages are currently loaded, not the whole scope.
 */
@AndroidEntryPoint
class NearbyScansActivity : AppCompatActivity() {

    private lateinit var binding: ActivityNearbyScansBinding
    private lateinit var adapter: NearbyScansAdapter

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var connectivity: ConnectivityObserver
    private val isRu by lazy { LocaleHelper.isRussian(this) }

    // Full feed as returned by the server; filtered + sorted client-side
    // whenever the user applies the overlay.
    private var allItems: List<CommunityScanItem> = emptyList()
    private var sortNewestFirst = true
    private var typeFilter: String? = null
    private var filterMenuItem: MenuItem? = null
    private var disableMenuItem: MenuItem? = null

    // Server-side scope filter: "nearby" (~30 km radius), "country", or
    // "worldwide" (no geo filter — needs no location).
    private var scope = "nearby"

    // Cursor pagination (Community v2's scrolling forum). nextBefore is the
    // server's cursor for the next (older) page; null once a page comes back
    // short, meaning the current scope is exhausted.
    private var nextBefore: Long? = null
    private var hasMorePages = true
    private var isLoadingPage = false
    private var currentRegion: ResolvedRegion? = null

    // Feed-scroll impressions already reported this screen session, so a
    // post scrolled past twice doesn't get reported twice from the client
    // side too (the server also dedupes, this just avoids the round trip).
    private val reportedImpressions = mutableSetOf<String>()

    // In-context opt-in: tapping Enable requests coarse location only, flips
    // community on, and reloads the feed. Notifications are deliberately NOT
    // part of joining — the POST_NOTIFICATIONS prompt fires later, at the one
    // place it's needed: when the user requests a second opinion on their
    // own scan (see CommunityController.beginRequest).
    private val communityPermissionLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission(),
    ) { _ ->
        com.plantdiseases.app.util.CommunityPrefs.enable(this)
        com.plantdiseases.app.push.PushManager.registerIfEnabled(this)
        load(isRefresh = false)
    }

    private fun requestCommunityOptIn() {
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

        val baseListPadding = resources.getDimensionPixelSize(R.dimen.space_2xl)
        ViewCompat.setOnApplyWindowInsetsListener(binding.appBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(top = bars.top)
            binding.rvNearby.updatePadding(bottom = bars.bottom + baseListPadding)
            insets
        }

        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.offlineNotice.observe(this, connectivity.isOnline)
        // Joining the community registers a push token and pulls the feed, so
        // it can't be done offline. The feed itself is guarded inside load().
        enableWhileOnline(connectivity.isOnline, binding.btnEnableCommunity)
        onReconnected(connectivity.isOnline) { load(isRefresh = false) }
        setupFilterMenu()

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
        binding.btnEnableCommunity.setOnClickListener { requestCommunityOptIn() }

        adapter = NearbyScansAdapter(
            onOpen = { item ->
                // Opening someone else's scan counts as a view (deduped per
                // viewer server-side).
                val pseudoId = TelemetryIds.userPseudoId(this)
                lifecycleScope.launch {
                    scanRepository.recordCommunityView(item.scanId, pseudoId)
                }
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    item,
                    DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
                )
            },
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
        val next = !item.likedByMe
        val optimisticCount = (item.likeCount + if (next) 1 else -1).coerceAtLeast(0)
        adapter.updateLike(item.scanId, optimisticCount, next)
        lifecycleScope.launch {
            scanRepository.setCommunityLike(item.scanId, TelemetryIds.userPseudoId(this@NearbyScansActivity), next)
                .onSuccess { adapter.updateLike(item.scanId, it.count, it.liked) }
                .onFailure { adapter.updateLike(item.scanId, item.likeCount, item.likedByMe) }
        }
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
            scanRepository.recordCommunityViews(newIds, TelemetryIds.userPseudoId(this@NearbyScansActivity))
        }
    }

    private fun setupFilterMenu() {
        binding.toolbar.inflateMenu(R.menu.nearby_scans_menu)
        filterMenuItem = binding.toolbar.menu.findItem(R.id.action_filter)
        disableMenuItem = binding.toolbar.menu.findItem(R.id.action_disable_community)
        // Hidden until a feed loads — filtering an empty screen is a no-op.
        filterMenuItem?.isVisible = false
        // "Leave community" only makes sense once the user is in it.
        disableMenuItem?.isVisible = CommunityPrefs.uploadsEnabled(this)
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_map -> {
                    startActivity(
                        android.content.Intent(this, CommunityMapActivity::class.java),
                    )
                    true
                }
                R.id.action_filter -> {
                    showFilterSheet()
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
                androidx.work.WorkManager.getInstance(this).cancelUniqueWork("opinion_check")
                androidx.work.WorkManager.getInstance(this).cancelUniqueWork("opinion_check_now")
                disableMenuItem?.isVisible = false
                showEnablePrompt()
            }
            .show()
    }

    private fun load(isRefresh: Boolean) {
        // Community is opt-in. Until the user opts in, show an Enable prompt
        // instead of reading their location / hitting the network.
        if (!CommunityPrefs.uploadsEnabled(this)) {
            showEnablePrompt()
            return
        }
        // The feed is live-only — nothing about it is cached. Offline, say so
        // instead of spinning through a request that can only time out (every
        // scope change and pull-to-refresh would pay for one). The observer
        // re-runs this the moment the server is reachable again.
        if (!connectivity.lastKnownOnline) {
            binding.swipeRefresh.isRefreshing = false
            showState(getString(R.string.no_network))
            return
        }
        disableMenuItem?.isVisible = true
        if (!isRefresh) showLoading()
        // Fresh (re)load starts the cursor over.
        nextBefore = null
        hasMorePages = true
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
            val result = fetchPage(before = null)
            binding.swipeRefresh.isRefreshing = false
            result.onSuccess { response ->
                nextBefore = response.nextBefore
                hasMorePages = response.nextBefore != null
                val others = excludeOwnScans(response.items)
                when {
                    others.isNotEmpty() -> showFeed(others)
                    // The server couldn't geo-scope the feed (no coords + no
                    // country) — usually community is on but coarse-location was
                    // never granted / has no fix yet. Prompt to enable location
                    // rather than claim there's nothing nearby.
                    response.locationRequired -> showLocationNeeded()
                    else -> showState(getString(R.string.similar_scans_empty))
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
        isLoadingPage = true
        lifecycleScope.launch {
            fetchPage(before = nextBefore)
                .onSuccess { response ->
                    nextBefore = response.nextBefore
                    hasMorePages = response.nextBefore != null
                    allItems = allItems + excludeOwnScans(response.items)
                    applyFilterAndSort()
                }
            isLoadingPage = false
        }
    }

    private suspend fun fetchPage(before: Long?) =
        scanRepository.communityNearby(
            country = currentRegion?.country,
            region = currentRegion?.locality,
            lat = currentRegion?.lat,
            lon = currentRegion?.lon,
            // No disease filter — the feed surfaces the whole scope.
            disease = null,
            userPseudoId = TelemetryIds.userPseudoId(this@NearbyScansActivity),
            limit = FEED_PAGE_SIZE,
            scope = scope,
            before = before,
        )

    // Belt-and-braces: also drop the user's OWN scans client-side. The
    // server already excludes by pseudo-id, but a scan uploaded under a
    // previous install's pseudo-id (after a reinstall) would slip through —
    // so we additionally hide any item whose globally unique scan_id matches
    // one in this device's local history.
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
        filterMenuItem?.isVisible = false
        disableMenuItem?.isVisible = false
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
        binding.cgScope.visibility = View.VISIBLE
        filterMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showLoading() {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.VISIBLE
        binding.ivStateIcon.visibility = View.GONE
        binding.tvState.text = getString(R.string.nearby_scans_loading)
        binding.btnEnableCommunity.visibility = View.GONE
        binding.cgScope.visibility = View.VISIBLE
        filterMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showState(message: String) {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.tvState.text = message
        binding.btnEnableCommunity.visibility = View.GONE
        binding.cgScope.visibility = View.VISIBLE
        filterMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showFeed(items: List<CommunityScanItem>) {
        allItems = items
        binding.btnEnableCommunity.visibility = View.GONE
        binding.cgScope.visibility = View.VISIBLE
        filterMenuItem?.isVisible = true
        applyFilterAndSort()
    }

    /** Apply the active type filter + date sort and hand the result to the adapter. */
    private fun applyFilterAndSort() {
        val filtered = typeFilter?.let { t -> allItems.filter { it.disease == t } } ?: allItems
        val sorted = if (sortNewestFirst) {
            filtered.sortedByDescending { it.createdAt }
        } else {
            filtered.sortedBy { it.createdAt }
        }
        binding.stateLayout.visibility = View.GONE
        binding.rvNearby.visibility = View.VISIBLE
        adapter.submit(sorted)
    }

    /**
     * Overlay to sort by date and filter by disease type. The type chips are
     * built from the diseases actually present in the loaded feed, so every
     * option yields results; the current selections are pre-checked.
     */
    private fun showFilterSheet() {
        if (allItems.isEmpty()) return
        val sheet = SheetNearbyFilterBinding.inflate(layoutInflater)
        val dialog = BottomSheetDialog(this)
        dialog.setContentView(sheet.root)

        sheet.cgDate.check(if (sortNewestFirst) R.id.chip_newest else R.id.chip_oldest)

        // "All" + each distinct disease present, ordered by localized label.
        val allChip = buildTypeChip(sheet.cgType, getString(R.string.filter_all), null)
        sheet.cgType.addView(allChip)
        var checkedTypeId = allChip.id
        allItems.map { it.disease }.distinct()
            .sortedBy { diseaseLabel(it) }
            .forEach { key ->
                val chip = buildTypeChip(
                    sheet.cgType,
                    DiseaseLabels.displayName(key, isRu) ?: key,
                    key,
                )
                sheet.cgType.addView(chip)
                if (key == typeFilter) checkedTypeId = chip.id
            }
        sheet.cgType.check(checkedTypeId)

        sheet.btnApply.setOnClickListener {
            sortNewestFirst = sheet.cgDate.checkedChipId != R.id.chip_oldest
            typeFilter = sheet.cgType.findViewById<Chip>(sheet.cgType.checkedChipId)
                ?.tag as? String
            applyFilterAndSort()
            dialog.dismiss()
        }
        dialog.show()
    }

    private fun buildTypeChip(parent: ViewGroup, label: String, key: String?): Chip {
        val chip = layoutInflater.inflate(R.layout.item_filter_chip, parent, false) as Chip
        chip.id = View.generateViewId()
        chip.text = label
        chip.tag = key
        return chip
    }

    private fun diseaseLabel(key: String): String =
        (DiseaseLabels.displayName(key, isRu) ?: key).lowercase()

    private companion object {
        // First page + every subsequent page. Small enough that a scroll-
        // triggered fetch feels instant; the server clamps to 50 regardless.
        const val FEED_PAGE_SIZE = 20
    }
}
