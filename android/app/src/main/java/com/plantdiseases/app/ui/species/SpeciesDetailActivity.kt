package com.plantdiseases.app.ui.species

import android.content.Context
import android.content.Intent
import android.graphics.Rect
import android.graphics.drawable.Drawable
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.StringRes
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import androidx.recyclerview.widget.LinearLayoutManager
import com.bumptech.glide.load.DataSource
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.engine.GlideException
import com.bumptech.glide.request.RequestListener
import com.bumptech.glide.request.target.Target
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.SpeciesDiseaseItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivitySpeciesDetailBinding
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.ui.common.ExpandableSectionAnimator
import com.plantdiseases.app.ui.common.bulletRow
import com.plantdiseases.app.ui.common.cardColumn
import com.plantdiseases.app.ui.common.cardHeading
import com.plantdiseases.app.ui.common.factRow
import com.plantdiseases.app.ui.common.sectionCard
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingContext
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingCoordinator
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.ui.result.SimilarScansAdapter
import com.plantdiseases.app.ui.widget.WorldMapView
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.FertiliserGuide
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.OnboardingStateStore
import com.plantdiseases.app.util.PlantCategory
import com.plantdiseases.app.util.PlantCategoryClassifier
import com.plantdiseases.app.util.PlantFacts
import com.plantdiseases.app.util.PlantTips
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.util.onConnectivityChanged
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import java.util.Locale
import javax.inject.Inject

/**
 * Full-screen species detail — the rich, scan-like target for both the
 * "Identify plant" flow and the Home "Trending species" carousel. Loads a
 * Wikipedia lead photo + extra reference photos + blurb (/api/species-info),
 * a healthy-baseline care summary (/api/care-plan), and the conditions the
 * species is commonly affected by (/api/species-diseases — AI, with tappable
 * links into the offline disease guide, falling back to the bundled guide
 * when the AI is unavailable). Offers add-to-garden / scan-health / Wikipedia.
 */
@AndroidEntryPoint
class SpeciesDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySpeciesDetailBinding

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var connectivity: ConnectivityObserver

    @Inject
    lateinit var onboardingState: OnboardingStateStore
    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
    private val locale: String get() = if (isRu) "ru" else "en"

    private lateinit var scientificName: String
    private var commonName: String? = null
    private var detailResolved = false
    private var careResolved = false
    private var profileResolved = false
    private var floweringAvailable = false
    private var nativeRangeAvailable = false
    private lateinit var onboardingCoordinator: OnboardingCoordinator

    /** Inputs to the "saved earlier" caption — see [applySavedDataNote]. */
    private var isOffline = false
    private var hasSpeciesContent = false

    /** Latest care plan / native-range summary seen so far, feeding [bindAboutFacts].
     *  loadCare() and loadSpeciesProfile() run concurrently and independently, so the
     *  fact card re-renders from whichever field is populated as each arrives. */
    private var carePlanSnapshot: CarePlanResponse? = null
    private var nativeRangeSummary: String? = null

    private val plantCategory: PlantCategory
        get() = PlantCategoryClassifier.classify(scientificName, commonName)

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySpeciesDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)
        onboardingCoordinator = OnboardingCoordinator(this, onboardingState)
        onboardingCoordinator.setContext(OnboardingContext.SPECIES_DETAILS)

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
        // This screen has real content offline (the species blurb, photos and
        // care plan are cached per species; common problems fall back to the
        // bundled guide), so it gets an inline "saved earlier" note rather
        // than an offline strip — and only once there is cached content to
        // caption.
        onConnectivityChanged(connectivity.isOnline) { online ->
            isOffline = !online
            applySavedDataNote()
        }

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
        setupExpandableProfileSections(savedInstanceState)
        bindAboutFacts()
        // Purely local — no load() call to pair with this, unlike the cards above.
        binding.lunarCalendar.setSpecies(scientificName, commonName)
        // Tapping an upcoming-day row selects a date whose detail panel may sit
        // below the fold; the calendar asks its host to bring that panel up.
        binding.lunarCalendar.onRequestScrollTo = { target -> scrollSpeciesTo(target) }

        // Independent loads run concurrently: the Wikipedia blurb/photo, care
        // plan, common-problems list and optional botanical profile. Each
        // renders as soon as it is ready, so one slow call never blanks the
        // whole screen.
        loadDetail()
        loadCare()
        loadDiseases()
        loadSpeciesProfile()
        loadCommunityScans()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        sections.forEach { outState.putBoolean(it.stateKey, it.expanded) }
        super.onSaveInstanceState(outState)
    }

    /**
     * One collapsible section: a card whose header row stays visible while its
     * body expands. Every section on this screen is that same shape, so the
     * behaviour is described once here rather than as one `setXExpanded()` per
     * section — there are seven of them.
     */
    private inner class Section(
        val stateKey: String,
        val card: ViewGroup,
        val header: View,
        val chevron: View,
        val body: View,
        @StringRes val titleRes: Int,
        @StringRes val expandRes: Int,
        @StringRes val collapseRes: Int,
        var expanded: Boolean,
    ) {
        fun render(animate: Boolean) {
            ExpandableSectionAnimator.update(
                container = card,
                expanded = expanded,
                chevron = chevron,
                animate = animate,
            ) {
                body.visibility = if (expanded) View.VISIBLE else View.GONE
            }
            header.contentDescription = getString(
                R.string.species_info_toggle_content_description,
                getString(titleRes),
                getString(if (expanded) collapseRes else expandRes),
            )
        }
    }

    private var sections: List<Section> = emptyList()

    /**
     * Builds the section list and wires each header. Defaults: About and Care
     * open, everything else closed — the screen should still answer "what is
     * this plant and how do I keep it alive?" without a single tap, while the
     * long tail (fertiliser, problems, flowering, range, lunar) stays folded
     * so the page is scannable.
     */
    private fun setupExpandableProfileSections(savedInstanceState: Bundle?) {
        fun restored(key: String, default: Boolean) =
            savedInstanceState?.getBoolean(key) ?: default

        sections = listOf(
            Section(
                STATE_ABOUT_EXPANDED, binding.cardAbout, binding.aboutHeader,
                binding.ivAboutChevron, binding.aboutBody,
                R.string.section_about_title, R.string.section_expand, R.string.section_collapse,
                restored(STATE_ABOUT_EXPANDED, default = true),
            ),
            Section(
                STATE_CARE_EXPANDED, binding.cardCare, binding.careHeader,
                binding.ivCareChevron, binding.careBody,
                R.string.section_care_title, R.string.section_expand, R.string.section_collapse,
                restored(STATE_CARE_EXPANDED, default = true),
            ),
            Section(
                STATE_FERTILISER_EXPANDED, binding.cardFertiliser, binding.fertiliserHeader,
                binding.ivFertiliserChevron, binding.fertiliserBody,
                R.string.section_fertiliser_title, R.string.section_expand, R.string.section_collapse,
                restored(STATE_FERTILISER_EXPANDED, default = false),
            ),
            Section(
                STATE_TIPS_EXPANDED, binding.cardTips, binding.tipsHeader,
                binding.ivTipsChevron, binding.tipsBody,
                R.string.section_tips_title, R.string.section_expand, R.string.section_collapse,
                restored(STATE_TIPS_EXPANDED, default = false),
            ),
            Section(
                STATE_DISEASES_EXPANDED, binding.cardDiseases, binding.diseasesHeader,
                binding.ivDiseasesSectionChevron, binding.diseasesBody,
                R.string.section_diseases_title, R.string.section_expand, R.string.section_collapse,
                restored(STATE_DISEASES_EXPANDED, default = false),
            ),
            Section(
                STATE_FLOWERING_EXPANDED, binding.cardFlowering, binding.floweringHeader,
                binding.ivFloweringChevron, binding.floweringBody,
                R.string.flowering_title, R.string.flowering_expand, R.string.flowering_collapse,
                restored(STATE_FLOWERING_EXPANDED, default = false),
            ),
            Section(
                STATE_NATIVE_RANGE_EXPANDED, binding.cardNativeRange, binding.nativeRangeHeader,
                binding.ivNativeRangeChevron, binding.nativeRangeBody,
                R.string.native_range_title, R.string.native_range_expand,
                R.string.native_range_collapse,
                restored(STATE_NATIVE_RANGE_EXPANDED, default = false),
            ),
            Section(
                STATE_LUNAR_EXPANDED, binding.cardLunar, binding.lunarHeader,
                binding.ivLunarChevron, binding.lunarBody,
                R.string.lunar_title, R.string.lunar_expand, R.string.lunar_collapse,
                restored(STATE_LUNAR_EXPANDED, default = false),
            ),
        )
        sections.forEach { section ->
            section.header.setOnClickListener { toggleSection(section) }
            section.render(animate = false)
        }
    }

    private fun toggleSection(section: Section) {
        section.expanded = !section.expanded
        section.render(animate = true)
        // Opening a section near the bottom would otherwise reveal its content
        // below the fold. Scroll its header up once the expand animation has
        // settled, so the freshly shown body is what the user is looking at.
        if (section.expanded) {
            section.card.postDelayed({ scrollSpeciesTo(section.card) }, SECTION_SCROLL_DELAY_MS)
        }
    }

    /**
     * Scrolls [target] to just under the app bar. Uses
     * `offsetDescendantRectToMyCoords`, which walks the whole parent chain, so
     * it stays correct however deeply the target is nested — a plain
     * `target.top` is relative to the immediate parent only.
     */
    private fun scrollSpeciesTo(target: View) {
        binding.speciesScroll.post {
            val rect = Rect()
            target.getDrawingRect(rect)
            binding.speciesScroll.offsetDescendantRectToMyCoords(target, rect)
            val margin = (SCROLL_MARGIN_DP * resources.displayMetrics.density).toInt()
            binding.speciesScroll.smoothScrollTo(0, (rect.top - margin).coerceAtLeast(0))
        }
    }

    /**
     * Fact card at the top of About: scientific name, plant group, native range
     * (once loaded) and a computed care difficulty. Rebuilt from scratch on every
     * call, so it is safe to invoke again whenever a later response (care plan,
     * species profile) fills in more of the picture.
     */
    private fun bindAboutFacts() {
        val host = binding.aboutFacts
        host.removeAllViews()
        val category = plantCategory

        val card = sectionCard()
        val column = cardColumn()
        card.addView(column)

        column.addView(
            factRow(R.drawable.ic_garden, getString(R.string.about_fact_scientific), scientificName),
        )
        column.addView(
            factRow(
                R.drawable.ic_guide,
                getString(R.string.about_fact_group),
                getString(categoryLabelRes(category)),
            ),
        )
        nativeRangeSummary?.let { range ->
            column.addView(factRow(R.drawable.ic_moon, getString(R.string.about_fact_range), range))
        }
        val difficultyRes = when (PlantFacts.difficulty(carePlanSnapshot, category)) {
            1 -> R.string.about_difficulty_easy
            2 -> R.string.about_difficulty_moderate
            else -> R.string.about_difficulty_demanding
        }
        column.addView(
            factRow(
                R.drawable.ic_shield_check,
                getString(R.string.about_fact_difficulty),
                getString(difficultyRes),
            ),
        )
        host.addView(card)
    }

    /** Maps a category onto the lunar_cat_* label the app already ships in both locales. */
    private fun categoryLabelRes(category: PlantCategory): Int = when (category) {
        PlantCategory.FRUITING -> R.string.lunar_cat_fruiting
        PlantCategory.ROOT -> R.string.lunar_cat_root
        PlantCategory.LEAFY -> R.string.lunar_cat_leafy
        PlantCategory.FLOWERING -> R.string.lunar_cat_flowering
        PlantCategory.SUCCULENT -> R.string.lunar_cat_succulent
        PlantCategory.FOLIAGE -> R.string.lunar_cat_foliage
        PlantCategory.HERB -> R.string.lunar_cat_herb
    }

    /**
     * Feeding section. Structure and defaults come from [FertiliserGuide] (keyed by
     * the same [PlantCategory] the lunar calendar uses); anything the care plan
     * actually supplied overrides the default. Nothing here costs a request, and —
     * unlike the old flat-text version — there is always something concrete to
     * show, so the card is unconditionally visible once the plan resolves.
     */
    private fun bindFertiliser(plan: CarePlanResponse) {
        binding.cardFertiliser.visibility = View.VISIBLE
        val host = binding.fertiliserBody
        // Keep the legacy TextView in the tree but stop using it, so no id disappears.
        binding.tvFertiliserBody.visibility = View.GONE
        // Remove any card added by a previous bind (index 0 is the legacy TextView).
        while (host.childCount > 1) host.removeViewAt(1)

        val guide = FertiliserGuide.forCategory(plantCategory)

        val schedule = sectionCard()
        val scheduleColumn = cardColumn()
        schedule.addView(scheduleColumn)
        scheduleColumn.addView(cardHeading(getString(R.string.feed_heading_schedule)))

        val weeks = if (plan.fertiliseWeeks > 0) plan.fertiliseWeeks else guide.intervalWeeks
        scheduleColumn.addView(
            factRow(
                R.drawable.ic_fertilize,
                getString(R.string.feed_row_interval),
                resources.getQuantityString(R.plurals.feed_every_weeks, weeks, weeks),
            ),
        )
        scheduleColumn.addView(
            factRow(
                R.drawable.ic_shield_check,
                getString(R.string.feed_row_npk),
                plan.fertiliserNpk?.takeIf { it.isNotBlank() } ?: guide.npk,
            ),
        )
        scheduleColumn.addView(
            factRow(
                R.drawable.ic_watering,
                getString(R.string.feed_row_dose),
                getString(R.string.feed_dose_percent, guide.dosePercent),
            ),
        )
        scheduleColumn.addView(
            factRow(
                R.drawable.ic_moon,
                getString(R.string.feed_row_season),
                monthRangeLabel(guide.seasonMonths),
            ),
        )
        scheduleColumn.addView(
            factRow(
                R.drawable.ic_garden,
                getString(R.string.feed_row_soil),
                plan.soilMix?.takeIf { it.isNotBlank() } ?: getString(guide.soilRes),
            ),
        )
        host.addView(schedule)

        val cautions = sectionCard()
        val cautionColumn = cardColumn()
        cautions.addView(cautionColumn)
        cautionColumn.addView(cardHeading(getString(R.string.feed_heading_cautions)))
        guide.cautionRes.forEach {
            cautionColumn.addView(bulletRow(getString(it), R.color.warning_amber))
        }
        FertiliserGuide.universalRules.forEach {
            cautionColumn.addView(bulletRow(getString(it)))
        }
        plan.seasonNote?.takeIf { it.isNotBlank() }?.let {
            cautionColumn.addView(bulletRow(it))
        }
        host.addView(cautions)
    }

    /** "March - September" from an IntRange of month numbers, or "all year round". */
    private fun monthRangeLabel(months: IntRange): String {
        val names = resources.getStringArray(R.array.flowering_month_names)
        return if (months.first == 1 && months.last == 12) {
            getString(R.string.feed_season_year_round)
        } else {
            getString(R.string.feed_season_range, names[months.first - 1], names[months.last - 1])
        }
    }

    /**
     * Tips section: local category advice first (always present, so the card is
     * unconditionally visible), then whatever free-text guidance the care plan
     * carried, then red flags in warning colour.
     */
    private fun bindTips(plan: CarePlanResponse) {
        binding.cardTips.visibility = View.VISIBLE
        val host = binding.tipsBody
        host.removeAllViews()

        val category = plantCategory

        val general = sectionCard()
        val generalColumn = cardColumn()
        general.addView(generalColumn)
        generalColumn.addView(cardHeading(getString(R.string.tips_heading_general)))
        resources.getStringArray(PlantTips.forCategory(category)).forEach {
            generalColumn.addView(bulletRow(it))
        }
        host.addView(general)

        val fromPlan = listOfNotNull(
            plan.keyAction,
            plan.placement,
            plan.acclimation,
            plan.wateringMethod,
        ).filter { it.isNotBlank() }
        if (fromPlan.isNotEmpty()) {
            val specific = sectionCard()
            val specificColumn = cardColumn()
            specific.addView(specificColumn)
            specificColumn.addView(cardHeading(getString(R.string.tips_heading_this_plant)))
            fromPlan.forEach { specificColumn.addView(bulletRow(it)) }
            host.addView(specific)
        }

        val flags = plan.redFlags.orEmpty().filter { it.isNotBlank() }
        if (flags.isNotEmpty()) {
            val warn = sectionCard()
            val warnColumn = cardColumn()
            warn.addView(warnColumn)
            warnColumn.addView(cardHeading(getString(R.string.tips_heading_red_flags)))
            flags.forEach { warnColumn.addView(bulletRow(it, R.color.disease_red)) }
            host.addView(warn)
        }
    }

    /**
     * "Other scans of this species" — moved here from the scan result screen
     * so every species surface lives on this page. Best-effort: the strip
     * stays hidden when the community has nothing for this species.
     */
    private fun loadCommunityScans() {
        val pseudoId = TelemetryIds.userPseudoId(this)
        val adapter = SimilarScansAdapter(
            displayName = { key -> DiseaseLabels.displayName(key, isRu) ?: key },
            // Compact preview cells; tapping opens the sheet — commenting is
            // unlocked here too (Community v2), guarded below for scans that
            // turn out to be this install's own.
            preview = true,
            onClick = { item ->
                if (item.scanId.isNotBlank() && pseudoId.isNotBlank()) {
                    lifecycleScope.launch { scanRepository.recordCommunityView(item.scanId, pseudoId) }
                }
                val label = DiseaseLabels.displayName(item.disease, isRu) ?: item.disease
                lifecycleScope.launch {
                    val mine = scanRepository.localClientScanIds()
                    if (item.scanId in mine) {
                        CommunityOpinionSheet.showForOwner(supportFragmentManager, item.scanId, label)
                    } else {
                        CommunityOpinionSheet.show(supportFragmentManager, item, label)
                    }
                }
            },
        )
        binding.rvSpeciesCommunity.layoutManager =
            LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        binding.rvSpeciesCommunity.adapter = adapter
        lifecycleScope.launch {
            scanRepository.communityScansBySpecies(scientificName, pseudoId).onSuccess { resp ->
                if (resp.items.isEmpty()) return@onSuccess
                adapter.submit(resp.items)
                binding.tvSpeciesCommunityLabel.visibility = View.VISIBLE
                binding.rvSpeciesCommunity.visibility = View.VISIBLE
            }
        }
    }

    private fun loadDetail() {
        binding.progress.visibility = View.VISIBLE
        binding.tvSpeciesExtract.text = getString(R.string.identify_loading)
        lifecycleScope.launch {
            // Cached per (name, locale) — reopening the same species serves
            // the stored Wikipedia blurb/photo instead of re-fetching.
            val info = scanRepository.getSpeciesInfo(scientificName, locale).getOrNull()
            binding.progress.visibility = View.GONE
            hasSpeciesContent = info?.available == true
            applySavedDataNote()
            if (info?.available == true) {
                binding.tvSpeciesExtract.text =
                    info.extract ?: getString(R.string.identify_no_info)
                revealExtractToggleIfClipped()
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
                binding.extractExpandRow.visibility = View.GONE
            }
            detailResolved = true
            registerOnboardingAnchorsWhenReady()
        }
    }

    /**
     * Caption the species text as saved-not-live, but only when both halves
     * are true: we're offline *and* something actually rendered. Offline with
     * no cached entry shows the ordinary "no info" empty state — calling that
     * "out of date" would be describing content that isn't there.
     */
    private fun applySavedDataNote() {
        binding.tvOfflineSavedNote.visibility =
            if (isOffline && hasSpeciesContent) View.VISIBLE else View.GONE
    }

    /** `lineCount` is only meaningful after layout, hence the post. */
    private fun revealExtractToggleIfClipped() {
        binding.tvSpeciesExtract.post {
            val collapsed = resources.getInteger(R.integer.species_extract_collapsed_lines)
            val clipped = binding.tvSpeciesExtract.lineCount > collapsed
            binding.extractExpandRow.visibility = if (clipped) View.VISIBLE else View.GONE
            if (clipped) {
                setExtractExpanded(expanded = false)
                binding.extractExpandRow.setOnClickListener {
                    setExtractExpanded(binding.tvSpeciesExtract.maxLines == collapsed)
                }
            }
        }
    }

    private fun setExtractExpanded(expanded: Boolean) {
        val collapsed = resources.getInteger(R.integer.species_extract_collapsed_lines)
        binding.tvSpeciesExtract.maxLines = if (expanded) Int.MAX_VALUE else collapsed
        binding.tvExtractExpand.setText(
            if (expanded) R.string.species_info_collapse else R.string.species_info_expand,
        )
        binding.ivExtractChevron.setImageResource(
            if (expanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
        )
        binding.extractExpandRow.contentDescription = getString(
            R.string.species_info_toggle_content_description,
            binding.tvSpeciesTitle.text?.toString().orEmpty(),
            getString(
                if (expanded) R.string.species_info_collapse else R.string.species_info_expand,
            ),
        )
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
            } else {
                careCard.showError(onRetry = { loadCare() })
            }
            // Pet toxicity renders as its own card (below), not part of the
            // care card. Hidden when the plan couldn't load / pin toxicity.
            bindToxicity(plan)
            // Feeding and tips guidance ride on the same response — no extra request.
            plan?.let {
                bindFertiliser(it)
                bindTips(it)
                carePlanSnapshot = it
                bindAboutFacts()
            }
            careResolved = true
            registerOnboardingAnchorsWhenReady()
        }
    }

    /** Pet-toxicity card — the same surface the scan result shows
     *  red when the species is a
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
                profileResolved = true
                registerOnboardingAnchorsWhenReady()
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
            floweringAvailable = hasFlowering

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
                val summary = localized?.nativeRangeSummary.orEmpty()
                    .ifBlank { nativeRange.summary.orEmpty() }
                    .trim()
                binding.tvNativeRangeSummary.text = summary
                binding.worldMap.setZones(zones)
                nativeRangeSummary = summary.takeIf { it.isNotBlank() }
                bindAboutFacts()
            }
            finishNativeRangeLoading(hasNativeRange)
            nativeRangeAvailable = hasNativeRange
            profileResolved = true
            registerOnboardingAnchorsWhenReady()
        }
    }

    /** Loading cards are never anchors; the whole catalog is exposed atomically. */
    private fun registerOnboardingAnchorsWhenReady() {
        if (!detailResolved || !careResolved || !profileResolved) return
        onboardingCoordinator.registerAnchor(
            OnboardingAnchorKey.SPECIES_SUMMARY,
            binding.speciesSummary,
        )
        onboardingCoordinator.registerAnchor(
            OnboardingAnchorKey.SPECIES_CARE,
            binding.careSlot,
        )
        onboardingCoordinator.registerAnchor(
            OnboardingAnchorKey.SPECIES_FLOWERING,
            binding.floweringHeader,
            available = { floweringAvailable && binding.cardFlowering.isShown },
        )
        onboardingCoordinator.registerAnchor(
            OnboardingAnchorKey.SPECIES_NATIVE_RANGE,
            binding.nativeRangeHeader,
            available = { nativeRangeAvailable && binding.cardNativeRange.isShown },
        )
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
        binding.listDiseases.visibility = vis
        applyDiseasesCollapse()
    }

    /**
     * The fallback path renders the whole bundled catalogue (17 rows today), so
     * cap the visible rows and hide the rest behind a toggle rather than ending
     * the screen in a wall of guide links.
     */
    private fun applyDiseasesCollapse() {
        val total = binding.listDiseases.childCount
        val cap = resources.getInteger(R.integer.species_problems_collapsed_rows)
        if (total <= cap) {
            binding.diseasesExpandRow.visibility = View.GONE
            return
        }
        binding.diseasesExpandRow.visibility = View.VISIBLE
        setDiseasesExpanded(expanded = false, total = total, cap = cap)
        binding.diseasesExpandRow.setOnClickListener {
            val nowCollapsed = binding.listDiseases.getChildAt(cap)?.visibility != View.VISIBLE
            setDiseasesExpanded(expanded = nowCollapsed, total = total, cap = cap)
        }
    }

    private fun setDiseasesExpanded(expanded: Boolean, total: Int, cap: Int) {
        for (i in cap until total) {
            binding.listDiseases.getChildAt(i)?.visibility =
                if (expanded) View.VISIBLE else View.GONE
        }
        binding.tvDiseasesExpand.text = if (expanded) {
            getString(R.string.list_show_fewer)
        } else {
            getString(R.string.list_show_all, total)
        }
        binding.ivDiseasesChevron.setImageResource(
            if (expanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
        )
        binding.diseasesExpandRow.contentDescription = getString(
            R.string.species_info_toggle_content_description,
            getString(R.string.species_common_problems),
            binding.tvDiseasesExpand.text?.toString().orEmpty(),
        )
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

    companion object {
        const val EXTRA_SCIENTIFIC_NAME = "extra_scientific_name"
        const val EXTRA_COMMON_NAME = "extra_common_name"
        private const val STATE_ABOUT_EXPANDED = "state_about_expanded"
        private const val STATE_CARE_EXPANDED = "state_care_expanded"
        private const val STATE_FERTILISER_EXPANDED = "state_fertiliser_expanded"
        private const val STATE_TIPS_EXPANDED = "state_tips_expanded"
        private const val STATE_DISEASES_EXPANDED = "state_diseases_expanded"
        private const val STATE_FLOWERING_EXPANDED = "state_flowering_expanded"
        private const val STATE_NATIVE_RANGE_EXPANDED = "state_native_range_expanded"
        private const val STATE_LUNAR_EXPANDED = "state_lunar_expanded"

        /** Let the 300 ms expand transition settle before scrolling, so the
         *  card has its final height and the scroll lands where it should. */
        private const val SECTION_SCROLL_DELAY_MS = 320L
        private const val SCROLL_MARGIN_DP = 8f

        fun start(context: Context, scientificName: String, commonName: String?) {
            context.startActivity(
                Intent(context, SpeciesDetailActivity::class.java)
                    .putExtra(EXTRA_SCIENTIFIC_NAME, scientificName)
                    .putExtra(EXTRA_COMMON_NAME, commonName),
            )
        }
    }
}
