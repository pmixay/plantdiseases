package com.plantdiseases.app.ui.community

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.StaggeredGridLayoutManager
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.chip.Chip
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
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
import com.plantdiseases.app.util.enableWhileOnline
import com.plantdiseases.app.util.onReconnected
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Social-style community feed. The server scopes it by country when it can,
 * then falls back globally (all diagnoses, not just the current scan's), and
 * renders one card per scan. Tapping a card or
 * its "Second opinion" action opens [CommunityOpinionSheet] — the vote
 * graph + opinions + the user's own vote.
 *
 * A toolbar filter action opens a bottom-sheet overlay to sort the feed
 * by date (newest / oldest) and narrow it to a single disease type.
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
        setupFilterMenu()
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
            onSecondOpinion = { item ->
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
        )
        binding.rvNearby.layoutManager =
            StaggeredGridLayoutManager(
                resources.getInteger(R.integer.community_span_count),
                StaggeredGridLayoutManager.VERTICAL,
            )
        binding.rvNearby.adapter = adapter

        binding.swipeRefresh.setColorSchemeResources(R.color.primary)
        binding.swipeRefresh.setOnRefreshListener { load(isRefresh = true) }

        load(isRefresh = false)
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
        if (!isRefresh) showLoading()
        lifecycleScope.launch {
            // Refresh location on every (re)load so "nearby" reflects where the
            // user actually is right now. Worldwide needs no anchor, so we skip
            // the location read entirely — the feed then works even when
            // coarse-location was never granted.
            val region = if (scope == "worldwide") {
                null
            } else {
                runCatching {
                    RegionResolver.resolveRegion(this@NearbyScansActivity)
                }.getOrNull()
            }
            val result = scanRepository.communityNearby(
                country = region?.country,
                region = region?.locality,
                lat = region?.lat,
                lon = region?.lon,
                // No disease filter — the feed surfaces the whole scope.
                disease = null,
                limit = 100,
                scope = scope,
            )
            binding.swipeRefresh.isRefreshing = false
            result.onSuccess { response ->
                when {
                    response.items.isNotEmpty() -> showFeed(response.items)
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
}
