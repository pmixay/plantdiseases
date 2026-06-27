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
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityNearbyScansBinding
import com.plantdiseases.app.databinding.SheetNearbyFilterBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject

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
class NearbyScansActivity : AppCompatActivity() {

    private lateinit var binding: ActivityNearbyScansBinding
    private lateinit var adapter: NearbyScansAdapter
    private val scanRepository: ScanRepository by inject()
    private val isRu by lazy { LocaleHelper.isRussian(this) }

    // Full feed as returned by the server; filtered + sorted client-side
    // whenever the user applies the overlay.
    private var allItems: List<CommunityScanItem> = emptyList()
    private var sortNewestFirst = true
    private var typeFilter: String? = null
    private var filterMenuItem: MenuItem? = null
    private var disableMenuItem: MenuItem? = null

    // Server-side scope filter: "nearby" (~30 km radius) or "country".
    private var scope = "nearby"

    // In-context opt-in: tapping Enable requests coarse location (+ notifications
    // on 13+), flips community on, and reloads the feed.
    private val communityPermissionLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions(),
    ) { _ ->
        com.plantdiseases.app.util.CommunityPrefs.enable(this)
        com.plantdiseases.app.push.PushManager.registerIfEnabled(this)
        load(isRefresh = false)
    }

    private fun requestCommunityOptIn() {
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

        // Nearby (~30 km) vs Country scope — reload the feed on change.
        binding.cgScope.setOnCheckedStateChangeListener { _, checked ->
            val next = if (checked.firstOrNull() == R.id.chip_scope_country) "country" else "nearby"
            if (next != scope) {
                scope = next
                load(isRefresh = false)
            }
        }
        binding.btnEnableCommunity.setOnClickListener { requestCommunityOptIn() }

        adapter = NearbyScansAdapter(
            displayName = { key -> DiseaseLabels.displayName(key, isRu) ?: key },
            onSecondOpinion = { item ->
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
        disableMenuItem?.isVisible = true
        if (!isRefresh) showLoading()
        lifecycleScope.launch {
            val pseudoId = TelemetryIds.userPseudoId(this@NearbyScansActivity)
            // Refresh location on every (re)load so "nearby" reflects where the
            // user actually is right now.
            val region = runCatching {
                RegionResolver.resolveRegion(this@NearbyScansActivity)
            }.getOrNull()
            val result = scanRepository.communityNearby(
                country = region?.country,
                region = region?.locality,
                lat = region?.lat,
                lon = region?.lon,
                // No disease filter — the feed surfaces the whole scope.
                disease = null,
                userPseudoId = pseudoId,
                limit = 100,
                scope = scope,
            )
            binding.swipeRefresh.isRefreshing = false
            result.onSuccess { response ->
                // Belt-and-braces: also drop the user's OWN scans client-side.
                // The server already excludes by pseudo-id, but a scan uploaded
                // under a previous install's pseudo-id (after a reinstall) would
                // slip through — so we additionally hide any item whose globally
                // unique scan_id matches one in this device's local history.
                val mine = scanRepository.localClientScanIds()
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
