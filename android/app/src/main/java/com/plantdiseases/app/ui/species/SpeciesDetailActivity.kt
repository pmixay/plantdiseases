package com.plantdiseases.app.ui.species

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.SpeciesDiseaseItem
import com.plantdiseases.app.databinding.ActivitySpeciesDetailBinding
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Full-screen species detail — the rich, scan-like target for both the
 * "Identify plant" flow and the Home "Trending species" carousel. Loads a
 * Wikipedia lead photo + extra reference photos + blurb (/api/species-info),
 * a healthy-baseline care summary (/api/care-plan), and the conditions the
 * species is commonly affected by (/api/species-diseases — AI, with tappable
 * links into the offline disease guide, falling back to the bundled guide
 * when the AI is unavailable). Offers add-to-garden / scan-health / Wikipedia.
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
            // Pad the app-bar container (not the fixed-height toolbar) so the
            // toolbar sits fully below the status bar instead of being squeezed.
            binding.appbar.updatePadding(top = bars.top)
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
        loadDiseases()
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
                        .diskCacheStrategy(DiskCacheStrategy.ALL)
                        .into(binding.ivSpeciesHero)
                }
                renderPhotos(info.images)
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

    /** Horizontal strip of extra reference photos from the species' Wikipedia
     *  media-list. Each thumbnail is rounded; no-op when the list is empty. */
    private fun renderPhotos(images: List<String>) {
        if (images.isEmpty()) return
        binding.photoStrip.removeAllViews()
        val density = resources.displayMetrics.density
        val size = (120 * density).toInt()
        val margin = (8 * density).toInt()
        images.take(6).forEach { url ->
            val card = MaterialCardView(this).apply {
                layoutParams = LinearLayout.LayoutParams(size, size).apply { marginEnd = margin }
                radius = 12 * density
                cardElevation = 0f
            }
            val iv = ImageView(this).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                )
                scaleType = ImageView.ScaleType.CENTER_CROP
                contentDescription = null
            }
            Glide.with(this).load(url).placeholder(R.drawable.ic_plant_placeholder)
                .diskCacheStrategy(DiskCacheStrategy.ALL).into(iv)
            card.addView(iv)
            binding.photoStrip.addView(card)
        }
        binding.photoStripScroll.visibility = View.VISIBLE
    }

    private fun loadDiseases() {
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            val resp = runCatching {
                app.apiClient.apiService
                    .speciesDiseases(scientificName, commonName, locale).body()
            }.getOrNull()
            val aiItems = if (resp?.available == true) {
                resp.diseases.filter { it.name.isNotBlank() }
            } else {
                emptyList()
            }
            renderDiseases(aiItems)
        }
    }

    /** Render the "common problems" section. Prefers the AI list (each item
     *  linked to a guide doc when it maps to one); falls back to the bundled
     *  disease guide so the section is never empty. */
    private fun renderDiseases(aiItems: List<SpeciesDiseaseItem>) {
        binding.listDiseases.removeAllViews()
        if (aiItems.isNotEmpty()) {
            binding.tvDiseasesSubtitle.setText(R.string.species_problems_ai_note)
            aiItems.forEach { item ->
                val guide = item.docKey?.let { DiseaseCatalog.guideItemForClass(it) }
                addDiseaseRow(
                    name = item.name,
                    note = item.note,
                    iconRes = guide?.iconRes ?: R.drawable.ic_disease,
                    guideId = guide?.id,
                )
            }
        } else {
            binding.tvDiseasesSubtitle.setText(R.string.species_problems_guide_note)
            DiseaseCatalog.diseaseGuideItems().forEach { g ->
                addDiseaseRow(
                    name = if (isRu) g.titleRu else g.titleEn,
                    note = if (isRu) g.descriptionRu else g.descriptionEn,
                    iconRes = g.iconRes,
                    guideId = g.id,
                )
            }
        }
        val hasRows = binding.listDiseases.childCount > 0
        val vis = if (hasRows) View.VISIBLE else View.GONE
        binding.tvDiseasesTitle.visibility = vis
        binding.tvDiseasesSubtitle.visibility = vis
        binding.listDiseases.visibility = vis
    }

    private fun addDiseaseRow(name: String, note: String?, iconRes: Int, guideId: String?) {
        val row = layoutInflater.inflate(
            R.layout.item_species_disease, binding.listDiseases, false,
        ) as MaterialCardView
        row.findViewById<ImageView>(R.id.disease_icon).setImageResource(iconRes)
        row.findViewById<TextView>(R.id.disease_name).text = name
        val noteView = row.findViewById<TextView>(R.id.disease_note)
        if (!note.isNullOrBlank()) {
            noteView.text = note
            noteView.visibility = View.VISIBLE
        }
        val chevron = row.findViewById<ImageView>(R.id.disease_chevron)
        if (guideId != null) {
            chevron.visibility = View.VISIBLE
            row.setOnClickListener { GuideDetailActivity.start(this, guideId) }
        } else {
            chevron.visibility = View.GONE
            row.isClickable = false
        }
        binding.listDiseases.addView(row)
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
