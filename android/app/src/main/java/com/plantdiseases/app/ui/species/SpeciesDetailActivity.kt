package com.plantdiseases.app.ui.species

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.databinding.ActivitySpeciesDetailBinding
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Full-screen species detail — the "detailed description on enter" target
 * for the Home "Trending species" carousel. Loads a Wikipedia lead photo +
 * blurb (/api/species-info) and a healthy-baseline care summary
 * (/api/care-plan), and offers add-to-garden / scan-health / Wikipedia.
 */
class SpeciesDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySpeciesDetailBinding
    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
    private val locale: String get() = if (isRu) "ru" else "en"

    private lateinit var scientificName: String
    private var commonName: String? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySpeciesDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.speciesToolbar.updatePadding(top = bars.top)
            insets
        }
        binding.speciesToolbar.setNavigationOnClickListener { finish() }

        scientificName = intent.getStringExtra(EXTRA_SCIENTIFIC_NAME).orEmpty()
        commonName = intent.getStringExtra(EXTRA_COMMON_NAME)?.takeIf { it.isNotBlank() }
        if (scientificName.isBlank()) {
            finish()
            return
        }

        val title = commonName ?: scientificName
        binding.speciesToolbar.title = title
        binding.tvSpeciesTitle.text = title
        binding.tvSpeciesScientific.text = scientificName

        binding.btnAddGarden.setOnClickListener { addToGarden() }
        binding.btnScanHealth.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    putExtra(MainActivity.EXTRA_OPEN_SCANNER, true)
                },
            )
        }

        loadDetail()
    }

    private fun loadDetail() {
        val app = application as PlantDiseasesApp
        binding.progress.visibility = View.VISIBLE
        binding.tvSpeciesExtract.text = getString(R.string.identify_loading)
        lifecycleScope.launch {
            val info = runCatching {
                app.apiClient.apiService.speciesInfo(scientificName, locale).body()
            }.getOrNull()
            binding.progress.visibility = View.GONE
            if (info?.available == true) {
                binding.tvSpeciesExtract.text =
                    info.extract ?: getString(R.string.identify_no_info)
                info.imageUrl?.let { url ->
                    Glide.with(this@SpeciesDetailActivity).load(url)
                        .placeholder(R.drawable.ic_plant_placeholder)
                        .into(binding.ivSpeciesHero)
                }
                info.pageUrl?.let { url ->
                    binding.btnWikipedia.visibility = View.VISIBLE
                    binding.btnWikipedia.setOnClickListener {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    }
                }
            } else {
                binding.tvSpeciesExtract.text = getString(R.string.identify_no_info)
            }

            val plan = runCatching {
                app.apiClient.apiService.carePlan(
                    CarePlanRequest(disease = "healthy", species = scientificName, locale = locale),
                ).body()
            }.getOrNull()
            if (plan != null) {
                val watering = when {
                    plan.wateringDaysIsAsNeeded -> getString(R.string.identify_water_as_needed)
                    plan.wateringDaysInt != null ->
                        getString(R.string.identify_water_days, plan.wateringDaysInt)
                    else -> null
                }
                val lines = listOfNotNull(
                    getString(R.string.identify_light_tier, prettyTier(plan.lightTier)),
                    watering,
                    plan.humidityTarget.takeIf { it.isNotBlank() }
                        ?.let { getString(R.string.identify_humidity, it) },
                    if (plan.toxicToPets) getString(R.string.identify_toxic_pets) else null,
                )
                if (lines.isNotEmpty()) {
                    binding.cardCare.visibility = View.VISIBLE
                    binding.tvCareBody.text = lines.joinToString("\n")
                }
            }
        }
    }

    private fun prettyTier(tier: String): String = when (tier) {
        "low" -> getString(R.string.identify_light_low)
        "medium" -> getString(R.string.identify_light_medium)
        "direct" -> getString(R.string.identify_light_direct)
        else -> getString(R.string.identify_light_bright_indirect)
    }

    private fun addToGarden() {
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            runCatching {
                app.plantRepository.savePlant(
                    name = commonName ?: scientificName,
                    species = scientificName,
                    scientificName = scientificName,
                    commonName = commonName,
                )
            }.onSuccess {
                Toast.makeText(
                    this@SpeciesDetailActivity, R.string.identify_added_garden, Toast.LENGTH_SHORT,
                ).show()
            }.onFailure {
                Toast.makeText(
                    this@SpeciesDetailActivity, R.string.identify_failed, Toast.LENGTH_SHORT,
                ).show()
            }
        }
    }

    companion object {
        const val EXTRA_SCIENTIFIC_NAME = "extra_scientific_name"
        const val EXTRA_COMMON_NAME = "extra_common_name"

        fun start(context: Context, scientificName: String, commonName: String?) {
            context.startActivity(
                Intent(context, SpeciesDetailActivity::class.java)
                    .putExtra(EXTRA_SCIENTIFIC_NAME, scientificName)
                    .putExtra(EXTRA_COMMON_NAME, commonName),
            )
        }
    }
}
