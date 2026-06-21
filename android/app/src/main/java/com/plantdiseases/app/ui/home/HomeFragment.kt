package com.plantdiseases.app.ui.home

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import com.bumptech.glide.Glide
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.TrendingResponse
import com.plantdiseases.app.databinding.FragmentHomeBinding
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.ui.camera.WaterCalculatorActivity
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.ui.stats.StatsTipsActivity
import com.plantdiseases.app.ui.symptom.SymptomCheckerActivity
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.RegionResolver
import kotlinx.coroutines.launch
import java.util.Calendar
import java.util.Locale

/**
 * Home — a visual discovery feed (start destination). Two hero actions, a
 * compact tools row, then the carousels that are the centerpiece: trending
 * species nearby (Wikipedia photos), popular diseases, and the disease
 * guide. A slim Stats & Tips teaser sits at the bottom. Tapping a card opens
 * a dedicated detail screen (SpeciesDetailActivity / GuideDetailActivity).
 */
class HomeFragment : Fragment() {

    private var _binding: FragmentHomeBinding? = null
    private val binding get() = _binding!!

    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
    private val locale: String get() = if (isRu) "ru" else "en"

    private val speciesAdapter by lazy {
        SpeciesCarouselAdapter(
            onClick = { SpeciesDetailActivity.start(requireContext(), it.species, null) },
            loadImage = ::loadSpeciesImage,
        )
    }
    private val popularAdapter by lazy {
        DiseaseCarouselAdapter { GuideDetailActivity.start(requireContext(), it.guideId) }
    }
    private val guideAdapter by lazy {
        DiseaseCarouselAdapter { GuideDetailActivity.start(requireContext(), it.guideId) }
    }

    // Memoised Wikipedia image URLs so re-binding a species card (or a
    // refresh) doesn't re-hit /api/species-info for a name already resolved.
    private val speciesImageCache = mutableMapOf<String, String?>()

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentHomeBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.tvGreeting.text = greeting()

        binding.cardScan.setOnClickListener { openScanTab() }
        binding.cardIdentify.setOnClickListener {
            startActivity(Intent(requireContext(), com.plantdiseases.app.ui.identify.SpeciesIdentifyActivity::class.java))
        }

        binding.tileLight.setOnClickListener {
            startActivity(Intent(requireContext(), LightMeterActivity::class.java))
        }
        binding.tileWater.setOnClickListener {
            startActivity(Intent(requireContext(), WaterCalculatorActivity::class.java))
        }
        binding.tileSymptom.setOnClickListener {
            startActivity(Intent(requireContext(), SymptomCheckerActivity::class.java))
        }
        binding.tileGuide.setOnClickListener { findNavController().navigate(R.id.guideFragment) }
        binding.tvGuideAll.setOnClickListener { findNavController().navigate(R.id.guideFragment) }

        binding.cardStats.setOnClickListener {
            startActivity(Intent(requireContext(), StatsTipsActivity::class.java))
        }

        binding.rvSpecies.layoutManager =
            LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false)
        binding.rvSpecies.adapter = speciesAdapter
        binding.rvPopular.layoutManager =
            LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false)
        binding.rvPopular.adapter = popularAdapter
        binding.rvGuide.layoutManager =
            LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false)
        binding.rvGuide.adapter = guideAdapter

        bindDiseaseGuide()
    }

    override fun onResume() {
        super.onResume()
        loadStatsSummary()
        loadTrending()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    /** Switch the bottom bar to the Scan tab (same transition as a tap). */
    private fun openScanTab() {
        requireActivity()
            .findViewById<BottomNavigationView>(R.id.bottom_navigation)
            ?.selectedItemId = R.id.cameraFragment
    }

    private fun greeting(): String {
        val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        return getString(
            when (hour) {
                in 5..11 -> R.string.home_greeting_morning
                in 12..17 -> R.string.home_greeting_day
                in 18..22 -> R.string.home_greeting_evening
                else -> R.string.home_greeting_night
            },
        )
    }

    // ── Disease guide carousel (local, always available) ────────────
    private fun bindDiseaseGuide() {
        val items = DiseaseCatalog.diseaseGuideItems().map { gi ->
            DiseaseCarouselAdapter.Item(
                guideId = gi.id,
                iconRes = gi.iconRes,
                title = if (isRu) gi.titleRu else gi.titleEn,
                subtitle = getString(R.string.home_guide_tag),
            )
        }
        guideAdapter.submit(items)
    }

    // ── Stats teaser ────────────────────────────────────────────────
    private fun loadStatsSummary() {
        val app = requireActivity().application as PlantDiseasesApp
        viewLifecycleOwner.lifecycleScope.launch {
            val scans = runCatching { app.scanRepository.getAllScans() }
                .getOrDefault(emptyList())
            val plants = runCatching { app.plantRepository.getPlants() }
                .getOrDefault(emptyList())
            val real = scans.filter { !it.isNotAPlant }
            val healthy = real.count { it.isHealthy }
            _binding?.tvStatsSummary?.text =
                getString(R.string.home_stats_summary, real.size, healthy, plants.size)
        }
    }

    // ── Trending carousels ──────────────────────────────────────────
    private fun loadTrending() {
        val app = requireActivity().application as PlantDiseasesApp
        val appContext = requireContext().applicationContext
        viewLifecycleOwner.lifecycleScope.launch {
            val region = runCatching { RegionResolver.resolveRegion(appContext) }.getOrNull()
            // Trending is a "moving" feed — cache it 24h (ResponseCache policy) so
            // every Home onResume doesn't re-hit the network for the same region.
            val cacheKey = "trending:v1:${region?.country.orEmpty()}|${region?.locality.orEmpty()}"
            val trending = com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, TrendingResponse::class.java)
                ?: runCatching {
                    app.apiClient.apiService.communityTrending(
                        country = region?.country, region = region?.locality,
                    ).body()
                }.getOrNull()?.also {
                    com.plantdiseases.app.util.ResponseCache.put(
                        cacheKey, it, com.plantdiseases.app.util.ResponseCache.TTL_TRENDING_MS,
                    )
                }
            val b = _binding ?: return@launch
            if (trending == null) {
                b.sectionSpecies.visibility = View.GONE
                b.sectionPopular.visibility = View.GONE
                return@launch
            }
            renderSpecies(b, trending)
            renderPopular(b, trending)
        }
    }

    private fun renderSpecies(b: FragmentHomeBinding, t: TrendingResponse) {
        if (t.topSpecies.isEmpty()) {
            b.sectionSpecies.visibility = View.GONE
            return
        }
        b.sectionSpecies.visibility = View.VISIBLE
        b.tvTrendingScope.text = scopeLabel(t)
        speciesAdapter.submit(t.topSpecies)
    }

    private fun renderPopular(b: FragmentHomeBinding, t: TrendingResponse) {
        // Only diseases we have a bundled doc for — guarantees the card has
        // an icon + localized name and the tap opens a real detail screen.
        val items = t.topDiseases.mapNotNull { d ->
            val gi = DiseaseCatalog.guideItemForClass(d.disease) ?: return@mapNotNull null
            DiseaseCarouselAdapter.Item(
                guideId = gi.id,
                iconRes = gi.iconRes,
                title = DiseaseLabels.displayName(d.disease, isRu)
                    ?: (if (isRu) gi.titleRu else gi.titleEn),
                subtitle = resources.getQuantityString(
                    R.plurals.home_disease_count, d.count, d.count,
                ),
            )
        }
        if (items.isEmpty()) {
            b.sectionPopular.visibility = View.GONE
            return
        }
        b.sectionPopular.visibility = View.VISIBLE
        popularAdapter.submit(items)
    }

    private fun scopeLabel(t: TrendingResponse): String = when (t.scope) {
        "region" -> listOfNotNull(t.region, t.country).joinToString(", ")
        "country" -> t.country ?: getString(R.string.home_trending_global)
        else -> getString(R.string.home_trending_global)
    }

    /** Resolve + cache a Wikipedia lead photo for a species, then load it
     *  into the card's ImageView. No-ops if the view was recycled away. */
    private fun loadSpeciesImage(scientificName: String, target: ImageView) {
        val cached = speciesImageCache[scientificName]
        if (cached != null) {
            Glide.with(target).load(cached).placeholder(R.drawable.ic_plant_placeholder).into(target)
            return
        }
        if (speciesImageCache.containsKey(scientificName)) return // resolved to null (no photo)
        val app = requireActivity().application as PlantDiseasesApp
        viewLifecycleOwner.lifecycleScope.launch {
            val url = runCatching {
                app.apiClient.apiService.speciesInfo(scientificName, locale).body()?.imageUrl
            }.getOrNull()
            speciesImageCache[scientificName] = url
            if (url != null) {
                Glide.with(target).load(url)
                    .placeholder(R.drawable.ic_plant_placeholder).into(target)
            }
        }
    }
}
