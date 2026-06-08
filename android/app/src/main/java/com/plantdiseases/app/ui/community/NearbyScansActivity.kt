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
import androidx.recyclerview.widget.StaggeredGridLayoutManager
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.chip.Chip
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.databinding.ActivityNearbyScansBinding
import com.plantdiseases.app.databinding.SheetNearbyFilterBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Social-style "Scans near you" feed. Resolves the user's approximate
 * region, pulls the regional community corpus (all diagnoses, not just
 * the current scan's), and renders one card per scan. Tapping a card or
 * its "Second opinion" action opens [CommunityOpinionSheet] — the vote
 * graph + opinions + the user's own vote.
 *
 * A toolbar filter action opens a bottom-sheet overlay to sort the feed
 * by date (newest / oldest) and narrow it to a single disease type.
 */
class NearbyScansActivity : AppCompatActivity() {

    private lateinit var binding: ActivityNearbyScansBinding
    private lateinit var adapter: NearbyScansAdapter
    private val isRu by lazy { LocaleHelper.isRussian(this) }

    // Full feed as returned by the server; filtered + sorted client-side
    // whenever the user applies the overlay.
    private var allItems: List<CommunityScanItem> = emptyList()
    private var sortNewestFirst = true
    private var typeFilter: String? = null
    private var filterMenuItem: MenuItem? = null

    // In-context community opt-in: tapping "Enable" requests coarse location
    // (+ notifications on 13+) then reloads the feed whatever the outcome.
    private val communityPermissionLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions(),
    ) { _ -> load(isRefresh = false) }

    private fun requestCommunityPermissions() {
        val perms = mutableListOf(android.Manifest.permission.ACCESS_COARSE_LOCATION)
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            perms += android.Manifest.permission.POST_NOTIFICATIONS
        }
        communityPermissionLauncher.launch(perms.toTypedArray())
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
        setupFilterMenu()

        adapter = NearbyScansAdapter(
            displayName = { key -> DiseaseLabels.displayName(key, isRu) ?: key },
            onSecondOpinion = { item ->
                // Opening someone else's scan counts as a view (deduped per
                // viewer server-side).
                val app = application as PlantDiseasesApp
                val pseudoId = TelemetryIds.userPseudoId(this)
                lifecycleScope.launch {
                    app.scanRepository.recordCommunityView(item.scanId, pseudoId)
                }
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    item.scanId,
                    DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
                )
            },
        )
        binding.rvNearby.layoutManager =
            StaggeredGridLayoutManager(2, StaggeredGridLayoutManager.VERTICAL)
        binding.rvNearby.adapter = adapter

        binding.swipeRefresh.setColorSchemeResources(R.color.primary)
        binding.swipeRefresh.setOnRefreshListener { load(isRefresh = true) }

        load(isRefresh = false)
    }

    private fun setupFilterMenu() {
        binding.toolbar.inflateMenu(R.menu.nearby_scans_menu)
        filterMenuItem = binding.toolbar.menu.findItem(R.id.action_filter)
        // Hidden until a feed loads — filtering an empty screen is a no-op.
        filterMenuItem?.isVisible = false
        binding.toolbar.setOnMenuItemClickListener { item ->
            if (item.itemId == R.id.action_filter) {
                showFilterSheet()
                true
            } else {
                false
            }
        }
    }

    private fun load(isRefresh: Boolean) {
        // Community is opt-in. Until the user opts in, show an Enable prompt
        // instead of reading their location / hitting the network.
        if (!com.plantdiseases.app.util.CommunityPrefs.uploadsEnabled(this)) {
            showEnableState()
            return
        }
        if (!isRefresh) showLoading()
        lifecycleScope.launch {
            val resolved = withContext(Dispatchers.IO) {
                RegionResolver.cachedRegion(this@NearbyScansActivity)
                    ?: RegionResolver.resolveRegion(this@NearbyScansActivity)
            }
            if (resolved == null) {
                binding.swipeRefresh.isRefreshing = false
                showState(getString(R.string.similar_scans_subtitle_no_region))
                return@launch
            }

            val app = application as PlantDiseasesApp
            val pseudoId = TelemetryIds.userPseudoId(this@NearbyScansActivity)
            val result = app.scanRepository.communityNearby(
                country = resolved.country,
                region = resolved.locality,
                // No disease filter — the feed surfaces the whole region.
                disease = null,
                userPseudoId = pseudoId,
                limit = 30,
            )
            binding.swipeRefresh.isRefreshing = false
            result.onSuccess { response ->
                // Belt-and-braces: also drop the user's OWN scans client-side.
                // The server already excludes by pseudo-id, but a scan uploaded
                // under a previous install's pseudo-id (after a reinstall) would
                // slip through — so we additionally hide any item whose globally
                // unique scan_id matches one in this device's local history.
                val mine = app.scanRepository.localClientScanIds()
                val others = response.items.filter { it.scanId !in mine }
                if (others.isEmpty()) {
                    showState(getString(R.string.similar_scans_empty))
                } else {
                    showFeed(others)
                }
            }.onFailure {
                showState(getString(R.string.community_opinion_error))
            }
        }
    }

    /** Opt-in prompt shown when community is OFF: a message + Enable button
     *  that flips the pref, asks for location/notifications, then reloads. */
    private fun showEnableState() {
        binding.swipeRefresh.isRefreshing = false
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.tvState.text = getString(R.string.similar_scans_enable_prompt)
        binding.btnEnableCommunity.visibility = View.VISIBLE
        binding.btnEnableCommunity.setOnClickListener {
            com.plantdiseases.app.util.CommunityPrefs.enable(this)
            binding.btnEnableCommunity.visibility = View.GONE
            requestCommunityPermissions()
        }
        filterMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showLoading() {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.VISIBLE
        binding.ivStateIcon.visibility = View.GONE
        binding.btnEnableCommunity.visibility = View.GONE
        binding.tvState.text = getString(R.string.nearby_scans_loading)
        filterMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showState(message: String) {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.btnEnableCommunity.visibility = View.GONE
        binding.tvState.text = message
        filterMenuItem?.isVisible = false
        binding.rvNearby.visibility = View.GONE
    }

    private fun showFeed(items: List<CommunityScanItem>) {
        allItems = items
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
                    sheet.cgType, DiseaseLabels.displayName(key, isRu) ?: key, key,
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
