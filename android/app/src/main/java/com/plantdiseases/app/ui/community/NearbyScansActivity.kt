package com.plantdiseases.app.ui.community

import android.content.Context
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.databinding.ActivityNearbyScansBinding
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
 * This is the prominent surface for the same corpus the result screen
 * shows as a compact carousel; here it gets full-width photos and room
 * to scroll.
 */
class NearbyScansActivity : AppCompatActivity() {

    private lateinit var binding: ActivityNearbyScansBinding
    private lateinit var adapter: NearbyScansAdapter
    private val isRu by lazy { LocaleHelper.isRussian(this) }

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

        adapter = NearbyScansAdapter(
            displayName = { key -> DiseaseLabels.displayName(key, isRu) ?: key },
            onSecondOpinion = { item ->
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    item.scanId,
                    DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
                )
            },
        )
        binding.rvNearby.layoutManager = LinearLayoutManager(this)
        binding.rvNearby.adapter = adapter

        binding.swipeRefresh.setColorSchemeResources(R.color.primary)
        binding.swipeRefresh.setOnRefreshListener { load(isRefresh = true) }

        load(isRefresh = false)
    }

    private fun load(isRefresh: Boolean) {
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
                if (response.items.isEmpty()) {
                    showState(getString(R.string.similar_scans_empty))
                } else {
                    showFeed(response.items)
                }
            }.onFailure {
                showState(getString(R.string.community_opinion_error))
            }
        }
    }

    private fun showLoading() {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.VISIBLE
        binding.ivStateIcon.visibility = View.GONE
        binding.tvState.text = getString(R.string.nearby_scans_loading)
        binding.rvNearby.visibility = View.GONE
    }

    private fun showState(message: String) {
        binding.stateLayout.visibility = View.VISIBLE
        binding.progress.visibility = View.GONE
        binding.ivStateIcon.visibility = View.VISIBLE
        binding.tvState.text = message
        binding.rvNearby.visibility = View.GONE
    }

    private fun showFeed(items: List<CommunityScanItem>) {
        binding.stateLayout.visibility = View.GONE
        binding.rvNearby.visibility = View.VISIBLE
        adapter.submit(items)
    }
}
