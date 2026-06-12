package com.plantdiseases.app.ui.home

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.chip.Chip
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.TrendingResponse
import com.plantdiseases.app.databinding.FragmentHomeBinding
import com.plantdiseases.app.databinding.ItemHomeToolBinding
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.ui.camera.WaterCalculatorActivity
import com.plantdiseases.app.ui.community.NearbyScansActivity
import com.plantdiseases.app.ui.identify.SpeciesIdentifyActivity
import com.plantdiseases.app.ui.stats.StatsTipsActivity
import com.plantdiseases.app.ui.symptom.SymptomCheckerActivity
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.RegionResolver
import kotlinx.coroutines.launch
import java.util.Calendar
import java.util.Locale

/**
 * Home — the app's front door (start destination). Two hero actions
 * (scan health / identify species), the tools grid (light meter, watering
 * helper, symptom check, disease guide), the community "trending nearby"
 * module and a Stats & Tips teaser. Everything here is a *launcher*: the
 * features themselves live in their own activities/fragments.
 */
class HomeFragment : Fragment() {

    private var _binding: FragmentHomeBinding? = null
    private val binding get() = _binding!!

    private val isRu: Boolean get() = Locale.getDefault().language == "ru"

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
            startActivity(Intent(requireContext(), SpeciesIdentifyActivity::class.java))
        }

        bindTool(binding.toolLight, R.drawable.ic_light, R.string.home_tool_light) {
            startActivity(Intent(requireContext(), LightMeterActivity::class.java))
        }
        bindTool(binding.toolWater, R.drawable.ic_watering, R.string.home_tool_water) {
            startActivity(Intent(requireContext(), WaterCalculatorActivity::class.java))
        }
        bindTool(binding.toolSymptom, R.drawable.ic_disease, R.string.home_tool_symptom) {
            startActivity(Intent(requireContext(), SymptomCheckerActivity::class.java))
        }
        bindTool(binding.toolGuide, R.drawable.ic_guide, R.string.home_tool_guide) {
            findNavController().navigate(R.id.guideFragment)
        }

        binding.btnTrendingCommunity.setOnClickListener {
            startActivity(Intent(requireContext(), NearbyScansActivity::class.java))
        }
        binding.cardStats.setOnClickListener {
            startActivity(Intent(requireContext(), StatsTipsActivity::class.java))
        }
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

    private fun bindTool(
        tool: ItemHomeToolBinding, icon: Int, title: Int, onClick: () -> Unit,
    ) {
        tool.toolIcon.setImageResource(icon)
        tool.toolTitle.setText(title)
        tool.root.setOnClickListener { onClick() }
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

    // ── Trending nearby ─────────────────────────────────────────────
    private fun loadTrending() {
        val app = requireActivity().application as PlantDiseasesApp
        val appContext = requireContext().applicationContext
        viewLifecycleOwner.lifecycleScope.launch {
            val region = runCatching { RegionResolver.resolveRegion(appContext) }
                .getOrNull()
            val trending = runCatching {
                app.apiClient.apiService.communityTrending(
                    country = region?.country, region = region?.locality,
                ).body()
            }.getOrNull()
            val b = _binding ?: return@launch
            if (trending == null || trending.total <= 0 ||
                (trending.topSpecies.isEmpty() && trending.topDiseases.isEmpty())
            ) {
                b.cardTrending.visibility = View.GONE
                return@launch
            }
            b.cardTrending.visibility = View.VISIBLE
            b.tvTrendingScope.text = scopeLabel(trending)
            renderTrendingSpecies(trending)
            renderTrendingDiseases(trending)
        }
    }

    private fun scopeLabel(t: TrendingResponse): String = when (t.scope) {
        "region" -> listOfNotNull(t.region, t.country).joinToString(", ")
        "country" -> t.country ?: getString(R.string.home_trending_global)
        else -> getString(R.string.home_trending_global)
    }

    private fun renderTrendingSpecies(t: TrendingResponse) {
        val group = binding.chipsTrendingSpecies
        group.removeAllViews()
        group.visibility = if (t.topSpecies.isEmpty()) View.GONE else View.VISIBLE
        t.topSpecies.forEach { s ->
            group.addView(Chip(requireContext()).apply {
                text = getString(R.string.home_trending_species_chip, s.species, s.count)
                isClickable = true
                setOnClickListener {
                    startActivity(
                        Intent(requireContext(), NearbyScansActivity::class.java),
                    )
                }
            })
        }
    }

    private fun renderTrendingDiseases(t: TrendingResponse) {
        val list = binding.listTrendingDiseases
        list.removeAllViews()
        val max = t.topDiseases.maxOfOrNull { it.count } ?: return
        t.topDiseases.take(3).forEach { d ->
            val label = DiseaseLabels.displayName(d.disease, isRu) ?: d.disease
            list.addView(TextView(requireContext()).apply {
                text = getString(R.string.home_trending_disease_row, label, d.count)
                textSize = 13f
                setPadding(0, 6, 0, 6)
                setTextColor(
                    requireContext().getColor(
                        if (d.count == max) R.color.on_surface else R.color.on_surface_secondary,
                    ),
                )
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                )
            })
        }
    }
}
