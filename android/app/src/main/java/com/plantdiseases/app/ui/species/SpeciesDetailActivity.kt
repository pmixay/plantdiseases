package com.plantdiseases.app.ui.species

import android.content.Context
import android.content.Intent
import android.graphics.drawable.Drawable
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
import com.bumptech.glide.load.DataSource
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.engine.GlideException
import com.bumptech.glide.request.RequestListener
import com.bumptech.glide.request.target.Target
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.SpeciesDiseaseItem
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivitySpeciesDetailBinding
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.ui.common.ExpandableSectionView
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.widget.WorldMapView
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
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
    private val scanRepository: ScanRepository by inject()
    private val plantRepository: PlantRepository by inject()
    private val connectivity: ConnectivityObserver by inject()
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
            // And pad the scroll content past the nav bar — without this the
            // bottom actions (Add to garden / Scan health) draw under the
            // system navigation bar and can't be scrolled into reach.
            binding.speciesScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.speciesToolbar.setNavigationOnClickListener { finish() }
        binding.offlineNotice.observe(this, connectivity)

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

        binding.lunarCalendar.setSpecies(scientificName, commonName)
        binding.lunarCalendar.onRequestScrollTo = { target -> scrollSpeciesTo(target) }

        attachSections()

        binding.btnAddGarden.setOnClickListener { addToGarden() }
        binding.btnScanHealth.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    putExtra(MainActivity.EXTRA_OPEN_SCANNER, true)
                },
            )
        }

        // Independent loads run concurrently: the Wikipedia blurb/photo, care
        // plan, common-problems list and optional botanical profile. Each
        // renders as soon as it is ready, so one slow call never blanks the
        // whole screen.
        loadDetail()
        loadCare()
        loadDiseases()
        loadSpeciesProfile()
    }

    private fun loadDetail() {
        binding.progress.visibility = View.VISIBLE
        binding.tvSpeciesExtract.text = getString(R.string.identify_loading)
        lifecycleScope.launch {
            // Cached per (name, locale) — reopening the same species serves
            // the stored Wikipedia blurb/photo instead of re-fetching.
            val info = scanRepository.getSpeciesInfo(scientificName, locale).getOrNull()
            binding.progress.visibility = View.GONE
            if (info?.available == true) {
                binding.tvSpeciesExtract.text =
                    info.extract ?: getString(R.string.identify_no_info)
                info.imageUrl?.let { url ->
                    Glide.with(this@SpeciesDetailActivity)
                        .load(com.plantdiseases.app.util.ServerConfig.photoUrl(url))
                        .placeholder(R.drawable.ic_plant_placeholder)
                        .diskCacheStrategy(DiskCacheStrategy.ALL)
                        .into(binding.ivSpeciesHero)
                }
                renderPhotos(info.images.orEmpty())
                info.pageUrl?.let { url ->
                    binding.btnWikipedia.visibility = View.VISIBLE
                    binding.btnWikipedia.setOnClickListener {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    }
                }
            } else {
                binding.tvSpeciesExtract.text = getString(R.string.identify_no_info)
            }
        }
    }

    /** Care-at-a-glance, on its own coroutine so it isn't gated behind the
     *  Wikipedia fetch. Renders the SAME rich [CarePlanCardView] the scan
     *  result and garden detail use (gradient header + watering/light/humidity
     *  pills + expandable details) instead of a flat text line. Reminders are
     *  hidden here — this is a healthy-baseline plan, not a diagnosis. */
    private fun loadCare() {
        val careCard = CarePlanCardView(this).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        }
        binding.careSlot.removeAllViews()
        binding.careSlot.addView(careCard)
        val speciesDisplay = commonName ?: scientificName
        careCard.showLoading(speciesDisplay)
        lifecycleScope.launch {
            val plan = scanRepository.fetchCarePlan(
                CarePlanRequest(disease = "healthy", species = scientificName, locale = locale),
            ).getOrNull()
            if (plan != null) {
                careCard.bind(
                    plan = plan,
                    speciesDisplay = speciesDisplay,
                    showApplyToReminders = false,
                )
                bindFertiliser(plan)
                bindTips(plan)
            } else {
                careCard.showError(onRetry = { loadCare() })
            }
            // Pet toxicity renders as its own card (below), not part of the
            // care card. Hidden when the plan couldn't load / pin toxicity.
            bindToxicity(plan)
        }
    }

    /** Feeding section — assembled from the care plan already on hand
     *  (fertilise_weeks / fertiliser_npk / soil_mix), so no extra request. */
    private fun bindFertiliser(plan: CarePlanResponse) {
        val lines = buildList {
            add(
                if (plan.fertiliseWeeks > 0) {
                    getString(R.string.fertiliser_interval, plan.fertiliseWeeks)
                } else {
                    getString(R.string.fertiliser_interval_none)
                },
            )
            plan.fertiliserNpk?.takeIf { it.isNotBlank() }
                ?.let { add(getString(R.string.fertiliser_npk, it)) }
            plan.soilMix?.takeIf { it.isNotBlank() }
                ?.let { add(getString(R.string.fertiliser_soil, it)) }
            plan.seasonNote?.takeIf { it.isNotBlank() }?.let { add(it) }
        }
        binding.tvFertiliserBody.text = lines.joinToString("\n\n")
    }

    /** Tips section — the care plan's free-text guidance, one paragraph per item. */
    private fun bindTips(plan: CarePlanResponse) {
        val lines = listOfNotNull(
            plan.keyAction, plan.placement, plan.acclimation, plan.wateringMethod,
        ).filter { it.isNotBlank() } + plan.redFlags.orEmpty().filter { it.isNotBlank() }
        binding.tvTipsBody.text = if (lines.isEmpty()) {
            getString(R.string.section_empty)
        } else {
            lines.joinToString("\n\n") { "• $it" }
        }
    }

    /**
     * Moves each pre-declared body block out of the hidden pool and into its
     * ExpandableSectionView. Re-parenting (rather than rebuilding) means every
     * existing loader — loadDetail / loadCare / loadDiseases /
     * loadSpeciesProfile — keeps writing to the very same view ids it always
     * has. All sections start collapsed.
     */
    private fun attachSections() {
        val pool = binding.sectionContentPool
        fun move(section: ExpandableSectionView, body: View, icon: Int, title: Int, subtitle: Int) {
            pool.removeView(body)
            body.visibility = View.VISIBLE
            section.bind(icon, title, subtitle)
            section.setContent(body)
        }
        move(
            binding.sectionAbout, binding.bodyAbout, R.drawable.ic_garden,
            R.string.section_about_title, R.string.section_about_subtitle,
        )
        move(
            binding.sectionCare, binding.bodyCare, R.drawable.ic_shield_check,
            R.string.section_care_title, R.string.section_care_subtitle,
        )
        move(
            binding.sectionDiseases, binding.bodyDiseases, R.drawable.ic_pest,
            R.string.section_diseases_title, R.string.section_diseases_subtitle,
        )
        move(
            binding.sectionFertiliser, binding.bodyFertiliser, R.drawable.ic_fertilize,
            R.string.section_fertiliser_title, R.string.section_fertiliser_subtitle,
        )
        move(
            binding.sectionLunar, binding.bodyLunar, R.drawable.ic_moon,
            R.string.section_lunar_title, R.string.section_lunar_subtitle,
        )
        move(
            binding.sectionTips, binding.bodyTips, R.drawable.ic_guide,
            R.string.section_tips_title, R.string.section_tips_subtitle,
        )
        pool.visibility = View.GONE

        // Expanding a section scrolls its header to the top so the freshly
        // revealed content isn't opened below the fold.
        listOf(
            binding.sectionAbout, binding.sectionCare, binding.sectionDiseases,
            binding.sectionFertiliser, binding.sectionLunar, binding.sectionTips,
        ).forEach { section ->
            section.onToggle = { expanded ->
                if (expanded) {
                    binding.speciesScroll.post {
                        val margin = (8 * resources.displayMetrics.density).toInt()
                        binding.speciesScroll.smoothScrollTo(0, (section.top - margin).coerceAtLeast(0))
                    }
                }
            }
        }
    }

    /** Scrolls the day-detail panel into view when a tap (e.g. an upcoming-day
     *  row) selects a date outside the current viewport. Uses on-screen
     *  deltas rather than walking the parent chain, so it stays correct
     *  regardless of how deeply [LunarCalendarView]'s body ends up nested
     *  after [attachSections] re-parents it. */
    private fun scrollSpeciesTo(target: View) {
        binding.speciesScroll.post {
            val targetLoc = IntArray(2)
            target.getLocationOnScreen(targetLoc)
            val scrollLoc = IntArray(2)
            binding.speciesScroll.getLocationOnScreen(scrollLoc)
            val margin = (24 * resources.displayMetrics.density).toInt()
            binding.speciesScroll.smoothScrollBy(0, targetLoc[1] - scrollLoc[1] - margin)
        }
    }

    /** Pet-toxicity card — the same surface the scan result shows
     *  (DiagnosisContentController.bindToxicity): red when the species is a
     *  known pet hazard, green when it's confirmed pet-safe, hidden when the
     *  server couldn't pin toxicity to a species (``source != "db"``). */
    private fun bindToxicity(plan: CarePlanResponse?) {
        val card = binding.cardToxicity
        if (plan == null || plan.toxicitySource != "db") {
            card.visibility = View.GONE
            return
        }
        card.visibility = View.VISIBLE
        if (plan.toxicToPets) {
            card.setCardBackgroundColor(getColor(R.color.disease_red_bg))
            binding.ivToxicityIcon.setImageResource(R.drawable.ic_warning)
            binding.ivToxicityIcon.setColorFilter(getColor(R.color.disease_red))
            binding.tvToxicityTitle.setTextColor(getColor(R.color.disease_red))
            binding.tvToxicityTitle.setText(R.string.toxicity_toxic_title)
            binding.tvToxicityBody.setTextColor(getColor(R.color.disease_red))
            binding.tvToxicityBody.text = getString(
                R.string.toxicity_toxic_body,
                plan.toxicSpeciesList.orEmpty().joinToString(", ") { localizeToxicSpecies(it) },
            )
        } else {
            card.setCardBackgroundColor(getColor(R.color.healthy_green_bg))
            binding.ivToxicityIcon.setImageResource(R.drawable.ic_healthy)
            binding.ivToxicityIcon.setColorFilter(getColor(R.color.healthy_green))
            binding.tvToxicityTitle.setTextColor(getColor(R.color.healthy_green))
            binding.tvToxicityTitle.setText(R.string.toxicity_safe_title)
            binding.tvToxicityBody.setTextColor(getColor(R.color.healthy_green))
            binding.tvToxicityBody.setText(R.string.toxicity_safe_body)
        }
    }

    /** Localize the ASPCA toxic-species tokens (a closed set: cats / dogs /
     *  horses); unknown tokens pass through unchanged. */
    private fun localizeToxicSpecies(token: String): String =
        when (token.trim().lowercase()) {
            "cats", "cat" -> getString(R.string.toxicity_animal_cats)
            "dogs", "dog" -> getString(R.string.toxicity_animal_dogs)
            "horses", "horse" -> getString(R.string.toxicity_animal_horses)
            else -> token
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
            Glide.with(this)
                .load(com.plantdiseases.app.util.ServerConfig.photoUrl(url))
                .placeholder(R.drawable.ic_plant_placeholder)
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                // Decode straight to the 120dp thumbnail size instead of the
                // full-resolution Wikipedia original — much faster to show and
                // far lighter on memory across a six-card strip.
                .override(size, size)
                .centerCrop()
                // Wikipedia media-lists can carry entries Glide can't render
                // (SVG, non-image media) or dead URLs. Drop those cards rather
                // than leaving a permanent placeholder so only real photos show.
                .listener(object : RequestListener<Drawable> {
                    override fun onLoadFailed(
                        e: GlideException?,
                        model: Any?,
                        target: Target<Drawable>,
                        isFirstResource: Boolean,
                    ): Boolean {
                        card.visibility = View.GONE
                        return false
                    }

                    override fun onResourceReady(
                        resource: Drawable,
                        model: Any,
                        target: Target<Drawable>,
                        dataSource: DataSource,
                        isFirstResource: Boolean,
                    ): Boolean = false
                })
                .into(iv)
            card.addView(iv)
            binding.photoStrip.addView(card)
        }
        binding.photoStripScroll.visibility = View.VISIBLE
    }

    private fun loadDiseases() {
        lifecycleScope.launch {
            // Peek the client cache first: a previously-opened species renders
            // instantly with NO loading flash, so reopening clearly feels cached
            // rather than re-fetched. Only a genuine miss shows the loading state.
            val cached = scanRepository.cachedSpeciesDiseases(scientificName, locale)
            if (cached != null) {
                // Render straight from the client cache — either a real AI list
                // or a cached "unavailable" marker that renders the bundled
                // guide. Either way: no loading flash and no network re-hit when
                // reopening the same species.
                renderDiseases(
                    if (cached.available) {
                        cached.diseases.orEmpty().filter { it.name.orEmpty().isNotBlank() }
                    } else {
                        emptyList()
                    },
                )
                return@launch
            }
            // Miss → reveal the section with a loading state so a slow (first-
            // time) AI lookup reads as "finding problems…", not an empty gap.
            binding.tvDiseasesTitle.visibility = View.VISIBLE
            binding.tvDiseasesSubtitle.visibility = View.VISIBLE
            binding.tvDiseasesSubtitle.setText(R.string.species_problems_loading)
            binding.pbDiseases.visibility = View.VISIBLE
            val resp = scanRepository
                .fetchSpeciesDiseases(scientificName, commonName, locale)
                .getOrNull()
            val aiItems = if (resp?.available == true) {
                resp.diseases.orEmpty().filter { it.name.orEmpty().isNotBlank() }
            } else {
                emptyList()
            }
            binding.pbDiseases.visibility = View.GONE
            renderDiseases(aiItems)
        }
    }

    /**
     * One optional botanical-profile request feeds both sections, but the UI
     * exposes separate loading states so users can see that both the flowering
     * calendar and native-range map are on their way. Each card resolves on its
     * own: valid content replaces its spinner, while absent/malformed content
     * hides only that card.
     */
    private fun loadSpeciesProfile() {
        showSpeciesProfileLoading()
        lifecycleScope.launch {
            val profile = scanRepository
                .fetchSpeciesProfile(scientificName, commonName, locale)
                .getOrNull()
            if (profile?.available != true) {
                finishFloweringLoading(hasContent = false)
                finishNativeRangeLoading(hasContent = false)
                return@launch
            }

            val localized = profile.localizedText.orEmpty()[locale]
            val flowering = profile.flowering
            val hasFlowering = flowering != null &&
                binding.floweringChart.setFlowering(flowering.monthlyIntensity.orEmpty())
            if (hasFlowering) {
                val note = localized?.indoorNote.orEmpty()
                    .ifBlank { flowering.indoorNote.orEmpty() }
                    .trim()
                binding.tvFloweringIndoorNote.text = note
                binding.tvFloweringIndoorNote.visibility =
                    if (note.isBlank()) View.GONE else View.VISIBLE

                val conditions = localized?.floweringConditions.orEmpty()
                    .ifBlank { profile.floweringConditions.orEmpty() }
                    .trim()
                if (conditions.isNotBlank()) {
                    binding.tvFloweringConditions.text = conditions
                    binding.cardFloweringConditions.visibility = View.VISIBLE
                }
            }
            finishFloweringLoading(hasFlowering)

            val nativeRange = profile.nativeRange
            val zones = nativeRange?.zones.orEmpty().mapNotNull { zone ->
                WorldMapView.ZoneStatus.fromWireName(zone.status.orEmpty())?.let { status ->
                    (zone.country.orEmpty().ifBlank { zone.countryCode.orEmpty() })
                        .takeIf(String::isNotBlank)?.let { country ->
                        WorldMapView.CountryZone(country, status, zone.countryCode)
                    }
                }
            }
            val hasNativeRange = nativeRange != null && zones.isNotEmpty()
            if (hasNativeRange) {
                binding.tvNativeRangeSummary.text = localized?.nativeRangeSummary.orEmpty()
                    .ifBlank { nativeRange.summary.orEmpty() }
                    .trim()
                binding.worldMap.setZones(zones)
            }
            finishNativeRangeLoading(hasNativeRange)
        }
    }

    private fun showSpeciesProfileLoading() {
        binding.cardFlowering.visibility = View.VISIBLE
        binding.floweringLoading.visibility = View.VISIBLE
        binding.floweringContent.visibility = View.GONE
        binding.cardFloweringConditions.visibility = View.GONE
        binding.tvFloweringIndoorNote.visibility = View.GONE

        binding.cardNativeRange.visibility = View.VISIBLE
        binding.nativeRangeLoading.visibility = View.VISIBLE
        binding.nativeRangeContent.visibility = View.GONE
    }

    private fun finishFloweringLoading(hasContent: Boolean) {
        binding.floweringLoading.visibility = View.GONE
        binding.floweringContent.visibility = if (hasContent) View.VISIBLE else View.GONE
        binding.cardFlowering.visibility = if (hasContent) View.VISIBLE else View.GONE
    }

    private fun finishNativeRangeLoading(hasContent: Boolean) {
        binding.nativeRangeLoading.visibility = View.GONE
        binding.nativeRangeContent.visibility = if (hasContent) View.VISIBLE else View.GONE
        binding.cardNativeRange.visibility = if (hasContent) View.VISIBLE else View.GONE
    }

    /** Render the "common problems" section. Prefers the AI list (each item
     *  linked to a guide doc when it maps to one); falls back to the bundled
     *  disease guide so the section is never empty. */
    private fun renderDiseases(aiItems: List<SpeciesDiseaseItem>) {
        binding.listDiseases.removeAllViews()
        if (aiItems.isNotEmpty()) {
            binding.tvDiseasesSubtitle.setText(R.string.species_problems_ai_note)
            aiItems.forEach { item ->
                // Match by class key first, then by display name, so the row
                // picks up the same icon as the disease guide/book instead of
                // the generic placeholder.
                val guide = item.docKey?.let { DiseaseCatalog.guideItemForClass(it) }
                    ?: DiseaseCatalog.guideItemForName(item.name)
                addDiseaseRow(
                    name = item.name.orEmpty(),
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
            R.layout.item_species_disease,
            binding.listDiseases,
            false,
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

    private fun addToGarden() {
        lifecycleScope.launch {
            runCatching {
                plantRepository.savePlant(
                    name = commonName ?: scientificName,
                    species = scientificName,
                    scientificName = scientificName,
                    commonName = commonName,
                )
            }.onSuccess {
                Toast.makeText(
                    this@SpeciesDetailActivity,
                    R.string.identify_added_garden,
                    Toast.LENGTH_SHORT,
                ).show()
            }.onFailure {
                Toast.makeText(
                    this@SpeciesDetailActivity,
                    R.string.identify_failed,
                    Toast.LENGTH_SHORT,
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
