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
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityPlantDetailBinding
import com.plantdiseases.app.ui.garden.detail.DetailExtraCardsController
import com.plantdiseases.app.ui.garden.detail.DetailHeaderController
import com.plantdiseases.app.ui.garden.detail.DetailJournalController
import com.plantdiseases.app.ui.garden.detail.DetailPhotosController
import com.plantdiseases.app.ui.garden.detail.DetailTimelineController
import com.plantdiseases.app.ui.garden.detail.dp
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject

/**
 * Thin host for the plant-detail screen. It owns the screen chrome (insets,
 * toolbar, edge-to-edge, scroll restore), loads the plant + its scans /
 * journal, and orchestrates the programmatically-built content by delegating
 * each section to a controller in [com.plantdiseases.app.ui.garden.detail]:
 * header/actions, the "extra cards" stack, and the Timeline / Photos / Journal
 * tabs. The camera / cover-pick [androidx.activity.result.ActivityResultLauncher]s
 * stay here because they must be registered before the activity is STARTED.
 */
class PlantDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPlantDetailBinding
    private lateinit var content: LinearLayout
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

    private var wateringSuggestion: PlantRepository.WateringSuggestion? = null
    private var lastWateredAt: Long? = null

    private val plantRepository: PlantRepository by inject()
    private val scanRepository: ScanRepository by inject()

    private val header by lazy {
        DetailHeaderController(
            activity = this,
            onReload = ::loadPlant,
            onRequestNewScan = ::requestNewScan,
            onOpenHero = { plant, scans -> photos.openHeroPhotoViewer(plant, scans) },
        )
    }
    private val extraCards by lazy {
        DetailExtraCardsController(
            activity = this,
            content = content,
            onReload = ::loadPlant,
            onClearWateringSuggestion = { wateringSuggestion = null },
        )
    }
    private val timeline by lazy { DetailTimelineController(this) }
    private val photos by lazy { DetailPhotosController(this) }
    private val journal by lazy { DetailJournalController(this, ::loadPlant) }

    /** "New scan" → opens the camera, runs the capture through the normal
     *  disease-analysis flow ([AnalysisActivity]) and auto-attaches the
     *  resulting scan to THIS plant (via EXTRA_PLANT_ID) so the photo lands
     *  in the plant's Photos + Timeline without a manual "Add to garden".
     *  Replaces the old journal-only gallery photo import — a "scan" means
     *  the camera. The captured file lives in the same plant_images dir the
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
    } catch (e: SecurityException) {
        android.util.Log.w("PlantDetail", "Persistable URI grant refused for $uri: $e")
        Toast.makeText(this, R.string.photo_picker_permission_refused, Toast.LENGTH_SHORT).show()
        false
    }

    /** Entry point for the "New scan" action tile: ensure the camera
     *  permission, then capture. */
    private fun requestNewScan() {
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
        buildLayout()
        loadPlant()
        // Best-effort scroll restore once the content has been laid out.
        if (restoredScrollY > 0) {
            binding.detailScroll.post { binding.detailScroll.scrollTo(0, restoredScrollY) }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::tabLayout.isInitialized) {
            outState.putInt(KEY_TAB, tabLayout.selectedTabPosition.coerceAtLeast(0))
        }
        if (::binding.isInitialized) {
            outState.putInt(KEY_SCROLL, binding.detailScroll.scrollY)
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

    private fun buildLayout() {
        // Edge-to-edge: let the gradient app bar fill the status-bar area.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityPlantDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)

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
        lifecycleScope.launch {
            val plant = plantRepository.getPlant(plantId)
            if (plant == null) {
                finish()
                return@launch
            }
            currentPlant = plant
            val scans = plantRepository.getScansForPlant(plantId)
            val journalEntries = plantRepository.getJournalEntries(plantId)
            wateringSuggestion = plantRepository.adaptiveWateringSuggestion(plantId)
            lastWateredAt = plantRepository.lastReminderDoneAt(plantId, "watering")
            render(plant, scans, journalEntries)
        }
    }

    private fun render(
        plant: PlantEntity,
        scans: List<ScanEntity>,
        journalEntries: List<PlantJournalEntity>,
    ) {
        if (::tabLayout.isInitialized) {
            restoredTabIndex = tabLayout.selectedTabPosition.coerceAtLeast(0)
        }
        header.bind(plantId, plant)
        extraCards.bindPlantId(plantId)

        content.removeAllViews()
        toolbarTitle.text = plant.name
        // Order is primary-first: identity & quick actions; the
        // time-sensitive nudge; the diagnosis; the care plan; the
        // contextual guide link; THEN the tabs. Light Meter +
        // Doctor used to live above the tabs but they're rare
        // touches — moving them below lets the user reach the core
        // data (Timeline / Photos / Journal) in one scroll.
        content.addView(header.buildHeader(plant, scans, journalEntries))
        content.addView(header.buildActionRow())
        extraCards.buildLastWateredStrip(lastWateredAt)?.let { content.addView(it) }
        extraCards.buildAdaptiveWateringChip(wateringSuggestion, currentPlant)?.let { content.addView(it) }
        extraCards.buildProgressionCard(scans, currentPlant)?.let { content.addView(it) }
        content.addView(extraCards.buildCarePlanSlot(plant, scans))
        extraCards.buildGuideLink(scans)?.let { content.addView(it) }
        content.addView(
            header.buildTabs(
                timelineContainer = { timelineContainer },
                photosContainer = { photosContainer },
                journalContainer = { journalContainer },
            ).also { tabLayout = it },
        )

        timelineContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp(this@PlantDetailActivity) }
        }
        journalContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp(this@PlantDetailActivity) }
        }
        photosContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp(this@PlantDetailActivity) }
        }
        content.addView(timelineContainer)
        content.addView(journalContainer)
        content.addView(photosContainer)
        applyRestoredTabSelection()

        // Secondary surfaces — pushed below the tabs so the
        // primary-actions area stays scannable.
        content.addView(extraCards.buildLightMeterEntry(plant))
        content.addView(extraCards.buildWaterCalculatorEntry(plant))
        content.addView(extraCards.buildDoctorEntry(plant, scans))

        timeline.renderTimeline(timelineContainer, plantId, scans)
        journal.renderJournal(journalContainer, plantId, journalEntries)
        photos.renderPhotos(photosContainer, scans, journalEntries)
    }

    private fun applyRestoredTabSelection() {
        if (restoredTabIndex in 1 until tabLayout.tabCount) {
            tabLayout.getTabAt(restoredTabIndex)?.select()
        }
    }

    companion object {
        const val EXTRA_PLANT_ID = "extra_plant_id"

        private const val KEY_TAB = "plant_detail_tab"
        private const val KEY_SCROLL = "plant_detail_scroll"
    }
}
