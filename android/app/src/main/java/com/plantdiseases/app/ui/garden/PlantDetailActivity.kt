package com.plantdiseases.app.ui.garden

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.StringRes
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.doOnNextLayout
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.billing.EntitlementRepository
import com.plantdiseases.app.billing.ProFeature
import com.plantdiseases.app.billing.ProUi
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityPlantDetailBinding
import com.plantdiseases.app.ui.garden.detail.DetailExtraCardsController
import com.plantdiseases.app.ui.garden.detail.DetailHeaderController
import com.plantdiseases.app.ui.garden.detail.DetailJournalController
import com.plantdiseases.app.ui.garden.detail.DetailPhotosController
import com.plantdiseases.app.ui.garden.detail.DetailTimelineController
import com.plantdiseases.app.ui.common.sectionRow
import com.plantdiseases.app.ui.garden.detail.dp
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingContext
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingCoordinator
import com.plantdiseases.app.ui.pro.ProActivity
import com.plantdiseases.app.ui.widget.LunarCalendarView
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStateStore
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.YearMonth
import javax.inject.Inject

/**
 * Thin host for the plant-detail screen. It owns the screen chrome (insets,
 * toolbar, edge-to-edge, scroll restore), loads the plant + its scans /
 * journal, and orchestrates the programmatically-built content by delegating
 * each section to a controller in [com.plantdiseases.app.ui.garden.detail]:
 * header/actions, the "extra cards" stack, and the Timeline / Photos / Journal
 * tabs. The camera / cover-pick [androidx.activity.result.ActivityResultLauncher]s
 * stay here because they must be registered before the activity is STARTED.
 */
@AndroidEntryPoint
class PlantDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPlantDetailBinding
    private lateinit var content: LinearLayout
    private lateinit var overviewContainer: LinearLayout
    private lateinit var careContainer: LinearLayout
    private lateinit var historyContainer: LinearLayout
    private lateinit var galleryContainer: LinearLayout
    private lateinit var timelineContainer: LinearLayout
    private lateinit var photosContainer: LinearLayout
    private lateinit var journalContainer: LinearLayout
    private lateinit var tabLayout: TabLayout
    private lateinit var toolbar: MaterialToolbar
    private lateinit var toolbarTitle: TextView
    private var plantId: Long = -1L
    private var currentPlant: PlantEntity? = null

    // UI state restored after rotation / process-death (the plant itself
    // reloads from the repository by plantId).
    private var restoredTabIndex = 0
    private var restoredScrollY = 0
    private var lunarNavigationState: LunarCalendarView.NavigationState? = null
    private var lunarCalendarView: LunarCalendarView? = null
    private var loadJob: Job? = null
    private var scrollRestorePosted = false

    private var wateringSuggestion: PlantRepository.WateringSuggestion? = null
    private var lastWateredAt: Long? = null

    @Inject
    lateinit var plantRepository: PlantRepository

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var careRepository: CareRepository

    @Inject
    lateinit var onboardingState: OnboardingStateStore

    @Inject
    lateinit var entitlementRepository: EntitlementRepository

    private lateinit var onboardingCoordinator: OnboardingCoordinator

    private val header by lazy {
        DetailHeaderController(
            activity = this,
            plantRepository = plantRepository,
            onReload = ::loadPlant,
            onRequestNewScan = ::requestNewScan,
            onOpenHero = { plant, scans -> photos.openHeroPhotoViewer(plant, scans) },
        )
    }
    private val extraCards by lazy {
        DetailExtraCardsController(
            activity = this,
            plantRepository = plantRepository,
            scanRepository = scanRepository,
            careRepository = careRepository,
            content = content,
            onReload = ::loadPlant,
            onClearWateringSuggestion = { wateringSuggestion = null },
        )
    }
    private val timeline by lazy { DetailTimelineController(this, plantRepository) }
    private val photos by lazy { DetailPhotosController(this) }
    private val journal by lazy { DetailJournalController(this, plantRepository, ::loadPlant) }

    /** "New scan" → lets the user pick camera or gallery, runs either
     *  through the normal disease-analysis flow ([AnalysisActivity]) and
     *  auto-attaches the resulting scan to THIS plant (via EXTRA_PLANT_ID)
     *  so the photo lands in the plant's Photos + Timeline without a manual
     *  "Add to garden". Both paths write into the same plant_images dir the
     *  in-app scanner uses, so the saved scan persists identically. */
    private var pendingScanFile: java.io.File? = null

    private val takeScan = registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val file = pendingScanFile
        pendingScanFile = null
        if (ok && file != null && file.exists()) {
            launchAnalysisForScan(file)
        } else {
            runCatching { file?.delete() }
        }
    }

    private val scanCameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            launchCameraScan()
        } else {
            Toast.makeText(
                this,
                R.string.camera_permission_needed,
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    /** Gallery-import branch of "New scan" — GetContent needs no runtime
     *  permission, so this has no camera-permission gate. Copies the picked
     *  Uri into our own files dir the same way CameraFragment's gallery
     *  import does (a transient content:// grant may not survive the
     *  activity hop), then runs it through the same analysis path as a
     *  fresh capture. */
    private val pickGalleryScan = registerForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        if (uri != null) importScanFromGallery(uri)
    }

    private fun importScanFromGallery(uri: Uri) {
        lifecycleScope.launch {
            val file = withContext(Dispatchers.IO) {
                ImageUtils.copyUriToFile(this@PlantDetailActivity, uri)?.also {
                    ImageUtils.downscaleForCropInPlace(it.absolutePath)
                }
            }
            if (file != null) {
                launchAnalysisForScan(file)
            } else {
                Toast.makeText(this@PlantDetailActivity, R.string.image_load_error, Toast.LENGTH_SHORT).show()
            }
        }
    }

    /** Cover-photo picker — the picked URI is persisted on
     *  [PlantEntity.coverPhotoUri] so the garden card thumbnail and
     *  detail-hero render against the user's chosen photo instead of
     *  the most-recent scan. Tapping Clear cover removes it again. */
    private val pickCover = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null && tryPersistRead(uri)) {
            lifecycleScope.launch {
                plantRepository.updateCoverPhoto(plantId, uri.toString())
                Toast.makeText(
                    this@PlantDetailActivity,
                    R.string.cover_photo_updated,
                    Toast.LENGTH_SHORT,
                ).show()
                loadPlant()
            }
        }
    }

    /** ``takePersistableUriPermission`` throws SecurityException on
     *  picker URIs whose content provider didn't actually grant the
     *  persistable flag — most system pickers do, some third-party
     *  file providers don't. Without this guard the picker callback
     *  silently crashed and the user saw nothing happen after picking
     *  a photo. Returns true when the URI is now safe to persist;
     *  false ⇒ caller should bail out of the flow. */
    private fun tryPersistRead(uri: Uri): Boolean = try {
        contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        true
    } catch (_: SecurityException) {
        // Do not log the URI: document-provider paths can contain user data.
        android.util.Log.w("PlantDetail", "Persistable URI grant refused")
        Toast.makeText(this, R.string.photo_picker_permission_refused, Toast.LENGTH_SHORT).show()
        false
    }

    /** Entry point for the "New scan" action tile: let the user choose a
     *  fresh camera capture or an existing photo, then run either through
     *  the same analysis path. Same icon + title + chevron row style as the
     *  "To Garden" attach dialog (GardenAttachController) rather than a bare
     *  setItems list, so the two choices read as tappable instead of as
     *  disabled-looking text floating over the hero card. */
    private fun requestNewScan() {
        lateinit var dialog: AlertDialog
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp(this@PlantDetailActivity), 4.dp(this@PlantDetailActivity), 20.dp(this@PlantDetailActivity), 4.dp(this@PlantDetailActivity))
        }
        column.addView(
            TextView(this).apply {
                text = getString(R.string.new_scan_hint)
                textSize = 13f
                setTextColor(getColor(R.color.on_surface))
                setPadding(0, 0, 0, 12.dp(this@PlantDetailActivity))
            },
        )
        column.addView(
            sectionRow(
                title = getString(R.string.take_photo),
                subtitle = null,
                iconRes = R.drawable.ic_scan,
                onClick = {
                    dialog.dismiss()
                    requestCameraScan()
                },
            ),
        )
        column.addView(
            sectionRow(
                title = getString(R.string.gallery),
                subtitle = null,
                iconRes = R.drawable.ic_gallery,
                onClick = {
                    dialog.dismiss()
                    pickGalleryScan.launch("image/*")
                },
            ),
        )
        dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.new_scan)
            .setView(column)
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun requestCameraScan() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            launchCameraScan()
        } else {
            scanCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    /** Mint a capture file in the plant_images dir (covered by
     *  file_paths.xml so FileProvider can grant it to the camera app) and
     *  fire the system camera. */
    private fun launchCameraScan() {
        val file = ImageUtils.createImageFile(this)
        pendingScanFile = file
        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        runCatching { takeScan.launch(uri) }.onFailure {
            pendingScanFile = null
            runCatching { file.delete() }
            Toast.makeText(this, R.string.image_load_error, Toast.LENGTH_SHORT).show()
        }
    }

    /** Run a freshly captured photo through the normal analysis flow,
     *  threading this plant's id so the saved scan is auto-attached on
     *  completion. The fresh scan shows up via onRestart → loadPlant when
     *  the user comes back. */
    private fun launchAnalysisForScan(file: java.io.File) {
        startActivity(
            Intent(
                this,
                com.plantdiseases.app.ui.analysis.AnalysisActivity::class.java,
            ).apply {
                putExtra(
                    com.plantdiseases.app.ui.analysis.AnalysisActivity.EXTRA_IMAGE_PATH,
                    file.absolutePath,
                )
                putExtra(
                    com.plantdiseases.app.ui.analysis.AnalysisActivity.EXTRA_PLANT_ID,
                    plantId,
                )
                // Garden scans must be server-diagnosed (at least gamma): an
                // offline Aqua scan can't be attached to a plant. Force the
                // user's server family, upgrading Aqua → gamma.
                val selected = com.plantdiseases.app.util.ModelFamilyPrefs.selected(
                    this@PlantDetailActivity,
                )
                val forced = if (selected.onDevice) {
                    com.plantdiseases.app.util.ModelFamily.GAMMA
                } else {
                    selected
                }
                putExtra(
                    com.plantdiseases.app.ui.analysis.AnalysisActivity.EXTRA_FORCE_FAMILY,
                    forced.id,
                )
            },
        )
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        plantId = intent.getLongExtra(EXTRA_PLANT_ID, -1L)
        if (plantId == -1L) {
            finish()
            return
        }
        restoredTabIndex = savedInstanceState?.getInt(KEY_TAB, 0) ?: 0
        restoredScrollY = savedInstanceState?.getInt(KEY_SCROLL, 0) ?: 0
        pendingScanFile = savedInstanceState
            ?.getString(KEY_PENDING_SCAN_PATH)
            ?.takeIf { it.isNotBlank() }
            ?.let { java.io.File(it) }
        lunarNavigationState = savedInstanceState?.let(::readLunarNavigationState)
        buildLayout()
        onboardingCoordinator = OnboardingCoordinator(this, onboardingState)
        loadPlant()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::tabLayout.isInitialized) {
            outState.putInt(KEY_TAB, tabLayout.selectedTabPosition.coerceAtLeast(0))
        }
        if (::binding.isInitialized) {
            // If the repository-backed content has not rendered yet, the
            // ScrollView is still empty and reports 0. Preserve the original
            // target through another recreation instead of overwriting it.
            outState.putInt(
                KEY_SCROLL,
                restoredScrollY.takeIf { it > 0 } ?: binding.detailScroll.scrollY,
            )
        }
        pendingScanFile?.absolutePath?.let { outState.putString(KEY_PENDING_SCAN_PATH, it) }
        (lunarCalendarView?.navigationState() ?: lunarNavigationState)?.let { state ->
            outState.putInt(KEY_LUNAR_YEAR, state.shownMonth.year)
            outState.putInt(KEY_LUNAR_MONTH, state.shownMonth.monthValue)
            outState.putLong(KEY_LUNAR_SELECTED_DAY, state.selectedDate.toEpochDay())
        }
    }

    override fun onRestart() {
        super.onRestart()
        // Coming back from another activity (Light Meter, a scan result,
        // the photo viewer…) — re-read the plant so this screen reflects
        // whatever just changed elsewhere (e.g. the lux reading the Light
        // Meter persisted in ITS onPause, or a freshly attached scan).
        // onRestart (not onResume) so the initial onCreate→loadPlant()
        // isn't doubled on first show.
        loadPlant()
    }

    override fun onResume() {
        super.onResume()
        entitlementRepository.refreshInBackground()
    }

    private fun buildLayout() {
        // Edge-to-edge: let the gradient app bar fill the status-bar area.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityPlantDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                entitlementRepository.state.collect { state ->
                    lunarCalendarView?.setProAccess(
                        ProUi.allows(state, ProFeature.LUNAR_FULL_CALENDAR),
                    ) { startActivity(ProActivity.intent(this@PlantDetailActivity)) }
                }
            }
        }

        content = binding.detailContent
        toolbar = binding.detailToolbar
        toolbarTitle = binding.detailToolbarTitle

        // Extend the gradient app bar behind the status bar, and keep the
        // scrollable content clear of the navigation bar at the bottom.
        binding.detailScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.detailAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.detailScroll.updatePadding(bottom = bars.bottom)
            insets
        }

        toolbar.apply {
            // Cancel the default toolbar content insets so the custom
            // title (declared in XML with a 48dp start margin to clear
            // the nav icon) sits flush against the 16dp gutter.
            setContentInsetStartWithNavigation(0)
            setContentInsetsAbsolute(0, 0)
            setContentInsetsRelative(0, 0)
            setPadding(16.dp(this@PlantDetailActivity), 0, 16.dp(this@PlantDetailActivity), 0)
            setNavigationIconTint(getColor(R.color.toolbar_title))
            setNavigationOnClickListener { finish() }
            inflateMenu(R.menu.plant_detail_menu)
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    R.id.action_set_cover_photo -> {
                        pickCover.launch(arrayOf("image/*"))
                        true
                    }
                    R.id.action_clear_cover_photo -> {
                        lifecycleScope.launch {
                            plantRepository.updateCoverPhoto(plantId, null)
                            loadPlant()
                        }
                        true
                    }
                    R.id.action_delete_plant -> {
                        header.confirmDeletePlant()
                        true
                    }
                    else -> false
                }
            }
        }
    }

    private fun loadPlant() {
        loadJob?.cancel()
        loadJob = lifecycleScope.launch {
            val plant = plantRepository.getPlant(plantId)
            if (plant == null) {
                // Reachable from a notification-centre row up to 30 days after
                // the plant was deleted — a bare finish() just flashes an empty
                // toolbar, so say what happened.
                Toast.makeText(
                    this@PlantDetailActivity,
                    R.string.plant_no_longer_available,
                    Toast.LENGTH_SHORT,
                ).show()
                finish()
                return@launch
            }
            currentPlant = plant
            val scans = plantRepository.getScansForPlant(plantId)
            val journalEntries = plantRepository.getJournalEntries(plantId)
            wateringSuggestion = plantRepository.adaptiveWateringSuggestion(plantId)
            lastWateredAt = plantRepository.lastReminderDoneAt(plantId, "watering")
            render(plant, scans, journalEntries)
            restoreScrollAfterContentRender()
        }
    }

    /**
     * The old onCreate `ScrollView.post` ran while the repository load was
     * still suspended and the content container was empty, so rotation always
     * snapped back to the top. Post only after render has rebuilt the complete
     * tab hierarchy, then consume the saved target exactly once.
     */
    private fun restoreScrollAfterContentRender() {
        val target = restoredScrollY
        if (target <= 0 || scrollRestorePosted) return
        scrollRestorePosted = true
        binding.detailScroll.doOnNextLayout {
            binding.detailScroll.scrollTo(0, target)
            restoredScrollY = 0
            scrollRestorePosted = false
        }
    }

    /** Overview / Care / History / Gallery, the Garden refactor's plant
     *  profile. History deliberately stacks Timeline
     *  then Journal rather than a single chronologically-interleaved
     *  feed: the two controllers keep their own independent rendering,
     *  which is far lower-risk than rewriting both to emit into one
     *  merged list without a working local compiler to verify it. */
    private suspend fun render(
        plant: PlantEntity,
        scans: List<ScanEntity>,
        journalEntries: List<PlantJournalEntity>,
    ) {
        // Defensive second boundary: repository reads are already filtered,
        // but a retained/legacy list must never feed a raw rejected argmax into
        // the plant header, health, timeline, photos, guide or care surfaces.
        val acceptedScans = scans.filterNot(plantRepository::isAbstainedScan)
        if (::tabLayout.isInitialized) {
            restoredTabIndex = tabLayout.selectedTabPosition.coerceAtLeast(0)
        }
        header.bind(plantId, plant)
        extraCards.bindPlantId(plantId)

        val activePlan = careRepository.activePlan(plant.id)
        val health = plantRepository.getPlantHealth(plant.id, acceptedScans, activePlan?.plan)

        lunarNavigationState = lunarCalendarView?.navigationState() ?: lunarNavigationState
        content.removeAllViews()
        toolbarTitle.text = plant.name
        val summaryAnchor = header.buildHeader(plant, acceptedScans, journalEntries, health)
        val actionsAnchor = header.buildActionRow()
        content.addView(summaryAnchor)
        content.addView(actionsAnchor)

        val historyAnchor = header.buildTabs(
            overviewContainer = { overviewContainer },
            careContainer = { careContainer },
            historyContainer = { historyContainer },
            galleryContainer = { galleryContainer },
        ).also { tabLayout = it }
        content.addView(historyAnchor)

        overviewContainer = tabContainer()
        careContainer = tabContainer().apply { visibility = View.GONE }
        historyContainer = tabContainer().apply { visibility = View.GONE }
        galleryContainer = tabContainer().apply { visibility = View.GONE }
        content.addView(overviewContainer)
        content.addView(careContainer)
        content.addView(historyContainer)
        content.addView(galleryContainer)
        applyRestoredTabSelection()

        // ── Overview ──────────────────────────────────────────────────
        overviewContainer.addView(extraCards.buildHealthCard(health))
        extraCards.buildTreatmentPlanCard(plant)?.let { overviewContainer.addView(it) }
        overviewContainer.addView(buildAddObservationEntry())
        extraCards.buildProgressionCard(acceptedScans, currentPlant)?.let { overviewContainer.addView(it) }
        extraCards.buildGuideLink(acceptedScans)?.let { overviewContainer.addView(it) }
        overviewContainer.addView(sectionLabel(R.string.lunar_title))
        overviewContainer.addView(
            LunarCalendarView(this).apply {
                setSpecies(plant.scientificName, plant.commonName ?: plant.species)
                setProAccess(entitlementRepository.allows(ProFeature.LUNAR_FULL_CALENDAR)) {
                    startActivity(ProActivity.intent(this@PlantDetailActivity))
                }
                lunarNavigationState?.let { state -> restoreNavigationState(state) }
                onRequestScrollTo = { target ->
                    val rect = android.graphics.Rect()
                    target.getDrawingRect(rect)
                    binding.detailScroll.offsetDescendantRectToMyCoords(target, rect)
                    binding.detailScroll.smoothScrollTo(0, rect.top)
                }
            }.also { lunarCalendarView = it },
        )

        // ── Care ──────────────────────────────────────────────────────
        val careAnchor = extraCards.buildCarePlanSlot(plant, acceptedScans)
        careContainer.addView(careAnchor)
        extraCards.buildLastWateredStrip(lastWateredAt)?.let { careContainer.addView(it) }
        extraCards.buildAdaptiveWateringChip(wateringSuggestion, currentPlant)?.let { careContainer.addView(it) }
        careContainer.addView(extraCards.buildLightMeterEntry(plant))
        careContainer.addView(extraCards.buildWaterCalculatorEntry(plant))
        val assistantAnchor = extraCards.buildDoctorEntry(plant)
        careContainer.addView(assistantAnchor)

        // ── History — Timeline then Journal, both visible together ──────
        timelineContainer = tabContainer()
        historyContainer.addView(sectionLabel(R.string.timeline))
        historyContainer.addView(timelineContainer)
        journalContainer = tabContainer()
        historyContainer.addView(sectionLabel(R.string.plant_journal))
        historyContainer.addView(journalContainer)
        timeline.renderTimeline(timelineContainer, plantId, acceptedScans)
        journal.renderJournal(journalContainer, plantId, journalEntries)

        // ── Gallery ───────────────────────────────────────────────────
        photosContainer = tabContainer()
        galleryContainer.addView(photosContainer)
        photos.renderPhotos(photosContainer, acceptedScans, journalEntries)

        onboardingCoordinator.registerAnchor(OnboardingAnchorKey.GARDEN_PLANT_SUMMARY, summaryAnchor)
        onboardingCoordinator.registerAnchor(OnboardingAnchorKey.GARDEN_PLANT_ACTIONS, actionsAnchor)
        onboardingCoordinator.registerAnchor(OnboardingAnchorKey.GARDEN_PLANT_CARE, careAnchor)
        onboardingCoordinator.registerAnchor(OnboardingAnchorKey.GARDEN_PLANT_HISTORY, historyAnchor)
        onboardingCoordinator.registerAnchor(
            OnboardingAnchorKey.GARDEN_PLANT_ASSISTANT,
            assistantAnchor,
            pauseAfterTarget = true,
        )
        onboardingCoordinator.setContext(OnboardingContext.GARDEN_PLANT_DETAILS)
    }

    private fun sectionLabel(@StringRes textRes: Int): View = TextView(this).apply {
        text = getString(textRes).uppercase()
        textSize = 12f
        letterSpacing = 0.06f
        setTextColor(getColor(R.color.on_surface))
        typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 16.dp(this@PlantDetailActivity) }
    }

    private fun tabContainer(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 14.dp(this@PlantDetailActivity) }
    }

    private fun buildAddObservationEntry(): View = TextView(this).apply {
        text = getString(R.string.add_observation)
        textSize = 13f
        setTextColor(getColor(R.color.primary))
        typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        setPadding(4.dp(this@PlantDetailActivity), 10.dp(this@PlantDetailActivity), 4.dp(this@PlantDetailActivity), 4.dp(this@PlantDetailActivity))
        isClickable = true
        isFocusable = true
        setOnClickListener {
            Haptics.tick(this@PlantDetailActivity)
            header.showNoteDialog()
        }
    }

    private fun applyRestoredTabSelection() {
        if (restoredTabIndex in 0 until tabLayout.tabCount) {
            tabLayout.getTabAt(restoredTabIndex)?.select()
        }
    }

    private fun readLunarNavigationState(bundle: Bundle): LunarCalendarView.NavigationState? {
        if (!bundle.containsKey(KEY_LUNAR_YEAR) || !bundle.containsKey(KEY_LUNAR_SELECTED_DAY)) return null
        return runCatching {
            LunarCalendarView.NavigationState(
                shownMonth = YearMonth.of(
                    bundle.getInt(KEY_LUNAR_YEAR),
                    bundle.getInt(KEY_LUNAR_MONTH),
                ),
                selectedDate = LocalDate.ofEpochDay(bundle.getLong(KEY_LUNAR_SELECTED_DAY)),
            )
        }.getOrNull()
    }

    companion object {
        const val EXTRA_PLANT_ID = "extra_plant_id"

        private const val KEY_TAB = "plant_detail_tab"
        private const val KEY_SCROLL = "plant_detail_scroll"
        private const val KEY_LUNAR_YEAR = "plant_detail_lunar_year"
        private const val KEY_LUNAR_MONTH = "plant_detail_lunar_month"
        private const val KEY_LUNAR_SELECTED_DAY = "plant_detail_lunar_selected_day"
        private const val KEY_PENDING_SCAN_PATH = "plant_detail_pending_scan_path"
    }
}
