package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.setPadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.appbar.AppBarLayout
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.tabs.TabLayout
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.util.ImageUtils.formatRelativeDate
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.io.File

class PlantDetailActivity : AppCompatActivity() {

    private lateinit var content: LinearLayout
    private lateinit var timelineContainer: LinearLayout
    private lateinit var journalContainer: LinearLayout
    private lateinit var emptyScans: LinearLayout
    private lateinit var emptyJournal: LinearLayout
    private lateinit var tabLayout: TabLayout
    private lateinit var toolbar: MaterialToolbar
    private lateinit var toolbarTitle: TextView
    private var plantId: Long = -1L
    private var currentPlant: PlantEntity? = null

    private val plantRepository: PlantRepository
        get() = (application as PlantDiseasesApp).plantRepository

    private val pickPhoto = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            showPhotoCaptionDialog(uri)
        }
    }

    /** Cover-photo picker — the picked URI is persisted on
     *  [PlantEntity.coverPhotoUri] so the garden card thumbnail and
     *  detail-hero render against the user's chosen photo instead of
     *  the most-recent scan. Tapping Clear cover removes it again. */
    private val pickCover = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            lifecycleScope.launch {
                plantRepository.updateCoverPhoto(plantId, uri.toString())
                Toast.makeText(this@PlantDetailActivity,
                    R.string.cover_photo_updated, Toast.LENGTH_SHORT).show()
                loadPlant()
            }
        }
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
        buildLayout()
        loadPlant()
    }

    private fun buildLayout() {
        val root = androidx.coordinatorlayout.widget.CoordinatorLayout(this).apply {
            setBackgroundColor(getColor(R.color.background))
        }

        val appBar = AppBarLayout(this).apply {
            layoutParams = androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams(
                androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams.MATCH_PARENT,
                androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams.WRAP_CONTENT,
            )
            setBackgroundResource(R.drawable.bg_toolbar_gradient)
            elevation = 0f
            ViewCompat.setOnApplyWindowInsetsListener(this) { view, insets ->
                val topInset = insets.getInsets(WindowInsetsCompat.Type.systemBars()).top
                view.setPadding(0, topInset, 0, 0)
                insets
            }
        }
        toolbar = MaterialToolbar(this).apply {
            layoutParams = AppBarLayout.LayoutParams(
                AppBarLayout.LayoutParams.MATCH_PARENT,
                64.dp(),
            )
            setContentInsetStartWithNavigation(0)
            setContentInsetsAbsolute(0, 0)
            setContentInsetsRelative(0, 0)
            setPadding(16.dp(), 0, 16.dp(), 0)
            setNavigationIcon(R.drawable.ic_back)
            setNavigationIconTint(getColor(R.color.toolbar_title))
            setTitleTextColor(getColor(R.color.toolbar_title))
            setTitleTextAppearance(this@PlantDetailActivity, R.style.TextAppearance_App_Toolbar)
            title = ""
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
                    else -> false
                }
            }
            toolbarTitle = TextView(this@PlantDetailActivity).apply {
                text = getString(R.string.plant_detail_title)
                textSize = 22f
                maxLines = 2
                ellipsize = android.text.TextUtils.TruncateAt.END
                setLineSpacing(1.dp().toFloat(), 1f)
                setTextColor(getColor(R.color.toolbar_title))
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL,
                )
                layoutParams = androidx.appcompat.widget.Toolbar.LayoutParams(
                    androidx.appcompat.widget.Toolbar.LayoutParams.MATCH_PARENT,
                    androidx.appcompat.widget.Toolbar.LayoutParams.WRAP_CONTENT,
                    Gravity.CENTER_VERTICAL,
                ).apply {
                    marginStart = 48.dp()
                    marginEnd = 0
                }
            }
            addView(toolbarTitle)
        }
        appBar.addView(toolbar)
        root.addView(appBar)

        val scroll = androidx.core.widget.NestedScrollView(this).apply {
            layoutParams = androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams(
                androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams.MATCH_PARENT,
                androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams.MATCH_PARENT,
            ).apply {
                behavior = AppBarLayout.ScrollingViewBehavior()
            }
            clipToPadding = false
        }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 16.dp(), 16.dp(), 32.dp())
        }
        scroll.addView(content)
        root.addView(scroll)
        setContentView(root)
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
            val journal = plantRepository.getJournalEntries(plantId)
            render(plant, scans, journal)
        }
    }

    private fun render(
        plant: PlantEntity,
        scans: List<ScanEntity>,
        journal: List<PlantJournalEntity>,
    ) {
        content.removeAllViews()
        toolbarTitle.text = plant.name
        content.addView(buildHeader(plant, scans, journal))
        content.addView(buildActionRow())
        content.addView(buildProgressionCard(scans))
        content.addView(buildCarePlanSlot(plant, scans))
        content.addView(buildLightMeterEntry(plant))
        content.addView(buildDoctorEntry(plant, scans))
        content.addView(buildTabs())

        timelineContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp() }
        }
        journalContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp() }
        }
        content.addView(timelineContainer)
        content.addView(journalContainer)

        renderTimeline(scans)
        renderJournal(journal)
    }

    private fun buildHeader(
        plant: PlantEntity,
        scans: List<ScanEntity>,
        journal: List<PlantJournalEntity>,
    ): View {
        val card = MaterialCardView(this).apply {
            radius = 24.dp().toFloat()
            cardElevation = 0f
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        val outer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        // Cover photo strip — user-picked first, latest-scan image
        // second, hide when neither exists so the text-only hero
        // still feels intentional rather than missing-image. The
        // gradient overlay at the bottom is rendered by the existing
        // bg_garden_hero drawable underneath, so the text remains
        // legible regardless of cover-photo brightness.
        val coverSrc: String? = plant.coverPhotoUri
            ?: scans.maxByOrNull { it.timestamp }?.imagePath
        if (coverSrc != null) {
            val cover = ImageView(this).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    160.dp(),
                )
                scaleType = ImageView.ScaleType.CENTER_CROP
                clipToOutline = true
                isClickable = true
                setOnClickListener { openHeroPhotoViewer(plant, scans) }
            }
            Glide.with(this).load(coverSrc).into(cover)
            outer.addView(cover)
        }
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp(), 22.dp(), 20.dp(), 20.dp())
            setBackgroundResource(R.drawable.bg_garden_hero)
        }
        body.addView(TextView(this).apply {
            text = plant.name
            textSize = 30f
            setTextColor(getColor(R.color.hero_stat_text))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            maxLines = 2
        })
        body.addView(TextView(this).apply {
            text = plant.species?.takeIf { it.isNotBlank() } ?: getString(R.string.plant_species_unknown)
            textSize = 15f
            setTextColor(getColor(R.color.hero_stat_text))
            alpha = 0.82f
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        })
        val stats = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 18.dp() }
        }
        stats.addView(statPill(getString(R.string.scans), scans.size.toString()))
        stats.addView(statPill(getString(R.string.journal), journal.size.toString()))
        stats.addView(statPill(getString(R.string.waterings), journal.count { it.type == PlantRepository.JournalType.WATERING.value }.toString(), endMargin = false))
        body.addView(stats)
        outer.addView(body)
        card.addView(outer)
        return card
    }

    private fun statPill(label: String, value: String, endMargin: Boolean = true): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            background = getDrawable(R.drawable.bg_plant_stat_pill)
            setPadding(10.dp(), 10.dp(), 10.dp(), 10.dp())
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { if (endMargin) marginEnd = 8.dp() }
        }
        box.addView(TextView(this).apply {
            text = value
            textSize = 18f
            setTextColor(getColor(R.color.hero_stat_text))
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        })
        box.addView(TextView(this).apply {
            text = label
            textSize = 11f
            setTextColor(getColor(R.color.hero_stat_text))
            alpha = 0.82f
        })
        return box
    }

    /** Horizontally-scrollable strip of per-plant action chips. The
     *  three "first-class" actions (Note / Photo / Water) keep their
     *  original visual weight; the four physiological-care actions
     *  (Fertilise / Repot / Prune / Mist) are reachable in one
     *  horizontal swipe. Reminders is pinned at the end. */
    private fun buildActionRow(): View {
        val scroller = android.widget.HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            overScrollMode = View.OVER_SCROLL_NEVER
            clipToPadding = false
            setPadding(0, 0, 0, 0)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = android.widget.HorizontalScrollView.LayoutParams(
                android.widget.HorizontalScrollView.LayoutParams.WRAP_CONTENT,
                android.widget.HorizontalScrollView.LayoutParams.WRAP_CONTENT,
            )
        }
        row.addView(actionTile(R.string.add_note, R.drawable.ic_note_pencil) { showNoteDialog() })
        row.addView(actionTile(R.string.add_photo_log, R.drawable.ic_gallery) { pickPhoto.launch(arrayOf("image/*")) })
        row.addView(actionTile(R.string.log_watering, R.drawable.ic_watering) { showWateringDialog() })
        row.addView(actionTile(R.string.log_fertilising, R.drawable.ic_treatment) {
            showLogDialog(JournalAction.FERTILISE)
        })
        row.addView(actionTile(R.string.log_pruning, R.drawable.ic_note_pencil) {
            showLogDialog(JournalAction.PRUNE)
        })
        row.addView(actionTile(R.string.log_repotting, R.drawable.ic_garden) {
            showLogDialog(JournalAction.REPOT)
        })
        row.addView(actionTile(R.string.log_misting, R.drawable.ic_warning) {
            showLogDialog(JournalAction.MIST)
        })
        row.addView(actionTile(R.string.reminders, R.drawable.ic_clock) {
            currentPlant?.let { ReminderScheduleSheet.show(supportFragmentManager, it) }
        })
        scroller.addView(row)
        return scroller
    }

    private fun actionTile(labelRes: Int, iconRes: Int, action: () -> Unit): View =
        MaterialCardView(this).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            isClickable = true
            foreground = selectable()
            contentDescription = getString(labelRes)
            layoutParams = LinearLayout.LayoutParams(78.dp(), 66.dp()).apply {
                marginStart = 4.dp()
                marginEnd = 4.dp()
            }
            setOnClickListener {
                Haptics.tick(this@PlantDetailActivity)
                action()
            }
            addView(LinearLayout(this@PlantDetailActivity).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                layoutParams = FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT,
                )
                setPadding(4.dp(), 7.dp(), 4.dp(), 6.dp())
                addView(ImageView(this@PlantDetailActivity).apply {
                    setImageResource(iconRes)
                    setColorFilter(getColor(R.color.primary))
                    layoutParams = LinearLayout.LayoutParams(24.dp(), 24.dp())
                })
                addView(TextView(this@PlantDetailActivity).apply {
                    text = getString(labelRes)
                    textSize = 11f
                    gravity = Gravity.CENTER
                    maxLines = 1
                    ellipsize = android.text.TextUtils.TruncateAt.END
                    setTextColor(getColor(R.color.on_surface))
                    typeface = android.graphics.Typeface.create(
                        "sans-serif-medium",
                        android.graphics.Typeface.NORMAL,
                    )
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 5.dp() }
                })
            })
        }

    /** Dialog kinds for the physiological-care journal actions. The
     *  existing watering / note / photo dialogs stay separate because
     *  each has its own bespoke flow (photo picker, etc.). */
    private enum class JournalAction(
        val titleRes: Int, val hintRes: Int,
    ) {
        FERTILISE(R.string.log_fertilising, R.string.fertilising_hint),
        PRUNE(R.string.log_pruning, R.string.pruning_hint),
        REPOT(R.string.log_repotting, R.string.repotting_hint),
        MIST(R.string.log_misting, R.string.misting_hint),
    }

    private fun showLogDialog(kind: JournalAction) {
        val input = TextInputEditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setHint(kind.hintRes)
            minLines = 2
            setSingleLine(false)
        }
        val container = TextInputLayout(this).apply {
            setPadding(20.dp(), 8.dp(), 20.dp(), 0)
            boxStrokeColor = getColor(R.color.primary)
            addView(input)
        }
        MaterialAlertDialogBuilder(this)
            .setTitle(kind.titleRes)
            .setView(container)
            .setPositiveButton(R.string.reminder_save) { _, _ ->
                val raw = input.text?.toString()
                lifecycleScope.launch {
                    when (kind) {
                        JournalAction.FERTILISE -> plantRepository.addFertilising(plantId, raw)
                        JournalAction.PRUNE -> plantRepository.addPruning(plantId, raw)
                        JournalAction.REPOT -> plantRepository.addRepotting(plantId, raw)
                        JournalAction.MIST -> plantRepository.addMisting(plantId, raw)
                    }
                    loadPlant()
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    /**
     * Care Plan slot. The card itself takes care of loading / error / filled
     * states; we just construct it, place it under the action row, and kick
     * off the network call.
     *
     * When the plant has no scans yet we deliberately skip the network call
     * and surface an "awaiting scan" placeholder. The server would happily
     * return a generic ``disease=unknown`` plan, but a "Monitor weekly"
     * card without any diagnosis context is misleading — it implies the
     * advice is tailored when it isn't. The CTA bounces the user to the
     * Scan tab so they can produce a real verdict before we recommend
     * anything specific.
     */
    private fun buildCarePlanSlot(plant: PlantEntity, scans: List<ScanEntity>): View {
        val card = CarePlanCardView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }

        val speciesHint = plant.scientificName?.takeIf { it.isNotBlank() }
            ?: plant.commonName?.takeIf { it.isNotBlank() }
            ?: plant.species?.takeIf { it.isNotBlank() }

        val latestScan = scans.maxByOrNull { it.timestamp }
        if (latestScan == null) {
            card.showAwaitingScan(speciesHint) {
                Toast.makeText(
                    this,
                    R.string.care_plan_awaiting_scan_toast,
                    Toast.LENGTH_SHORT,
                ).show()
                finish()
            }
            return card
        }

        card.showLoading(speciesHint)
        val classKey = resolveClassKey(latestScan)
        val isRu = LocaleHelper.isRussian(this)

        lifecycleScope.launch {
            val app = application as PlantDiseasesApp
            val request = CarePlanRequest(
                disease = classKey,
                species = speciesHint,
                light = "bright_indirect",
                locale = if (isRu) "ru" else "en",
            )
            val result = app.scanRepository.fetchCarePlan(request)
            result.onSuccess { plan ->
                card.bind(
                    plan = plan,
                    speciesDisplay = speciesHint,
                    showApplyToReminders = true,
                    onApplyToReminders = { applied ->
                        ReminderScheduleSheet.showWithCarePlanDefaults(
                            supportFragmentManager, plant, applied,
                        )
                    },
                )
            }.onFailure {
                card.showError(onRetry = {
                    val replacement = buildCarePlanSlot(plant, scans)
                    val idx = content.indexOfChild(card)
                    if (idx >= 0) {
                        content.removeViewAt(idx)
                        content.addView(replacement, idx)
                    }
                })
            }
        }
        return card
    }

    /**
     * Best-effort: read the disease class key from the scan's stored top
     * probability (canonical model class name like "powdery_mildew"); fall
     * back to a normalised lowercase of the human-readable name.
     */
    private fun resolveClassKey(scan: ScanEntity): String {
        val app = application as PlantDiseasesApp
        return app.scanRepository.parseAllProbs(scan.allProbs)
            .entries.maxByOrNull { it.value }?.key
            ?: scan.diseaseName.lowercase().replace(' ', '_')
    }

    /** Light-meter shortcut. Launches the standalone
     *  [com.plantdiseases.app.ui.camera.LightMeterActivity] (which
     *  already ships under Profile) so the user can verify the actual
     *  lux at the plant's location. */
    private fun buildLightMeterEntry(plant: PlantEntity): View =
        MaterialCardView(this).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            isClickable = true
            foreground = selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener {
                Haptics.tick(this@PlantDetailActivity)
                startActivity(Intent(
                    this@PlantDetailActivity,
                    com.plantdiseases.app.ui.camera.LightMeterActivity::class.java,
                ))
            }
            addView(LinearLayout(this@PlantDetailActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
                addView(FrameLayout(this@PlantDetailActivity).apply {
                    setBackgroundResource(R.drawable.bg_icon_circle)
                    layoutParams = LinearLayout.LayoutParams(42.dp(), 42.dp())
                    addView(ImageView(this@PlantDetailActivity).apply {
                        setImageResource(R.drawable.ic_light)
                        setColorFilter(getColor(R.color.primary))
                        layoutParams = FrameLayout.LayoutParams(23.dp(), 23.dp(), Gravity.CENTER)
                    })
                })
                addView(LinearLayout(this@PlantDetailActivity).apply {
                    orientation = LinearLayout.VERTICAL
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                        .apply { marginStart = 12.dp() }
                    addView(TextView(this@PlantDetailActivity).apply {
                        text = getString(R.string.light_meter_entry_title)
                        textSize = 15f
                        setTextColor(getColor(R.color.on_surface))
                        typeface = android.graphics.Typeface.create(
                            "sans-serif-medium",
                            android.graphics.Typeface.NORMAL,
                        )
                    })
                    addView(TextView(this@PlantDetailActivity).apply {
                        text = getString(R.string.light_meter_entry_subtitle)
                        textSize = 12f
                        setTextColor(getColor(R.color.on_surface_secondary))
                        maxLines = 2
                        ellipsize = android.text.TextUtils.TruncateAt.END
                        layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            LinearLayout.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = 2.dp() }
                    })
                })
                addView(ImageView(this@PlantDetailActivity).apply {
                    setImageResource(R.drawable.ic_chevron_right)
                    setColorFilter(getColor(R.color.on_surface_secondary))
                    layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                })
            })
        }

    private fun buildDoctorEntry(plant: PlantEntity, scans: List<ScanEntity>): View =
        MaterialCardView(this).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            isClickable = true
            foreground = selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener {
                Haptics.tick(this@PlantDetailActivity)
                DoctorChatSheet.show(
                    supportFragmentManager,
                    plant,
                    scans.maxByOrNull { it.timestamp }?.diseaseName,
                )
            }
            addView(LinearLayout(this@PlantDetailActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
                addView(FrameLayout(this@PlantDetailActivity).apply {
                    setBackgroundResource(R.drawable.bg_icon_circle)
                    layoutParams = LinearLayout.LayoutParams(42.dp(), 42.dp())
                    addView(ImageView(this@PlantDetailActivity).apply {
                        setImageResource(R.drawable.ic_care)
                        setColorFilter(getColor(R.color.primary))
                        layoutParams = FrameLayout.LayoutParams(23.dp(), 23.dp(), Gravity.CENTER)
                    })
                })
                addView(LinearLayout(this@PlantDetailActivity).apply {
                    orientation = LinearLayout.VERTICAL
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                        .apply { marginStart = 12.dp() }
                    addView(TextView(this@PlantDetailActivity).apply {
                        text = getString(R.string.doctor_title)
                        textSize = 15f
                        setTextColor(getColor(R.color.on_surface))
                        typeface = android.graphics.Typeface.create(
                            "sans-serif-medium",
                            android.graphics.Typeface.NORMAL,
                        )
                    })
                    addView(TextView(this@PlantDetailActivity).apply {
                        text = getString(R.string.doctor_stub_subtitle)
                        textSize = 12f
                        setTextColor(getColor(R.color.on_surface_secondary))
                        maxLines = 1
                        ellipsize = android.text.TextUtils.TruncateAt.END
                        layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            LinearLayout.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = 2.dp() }
                    })
                })
                addView(ImageView(this@PlantDetailActivity).apply {
                    setImageResource(R.drawable.ic_chevron_right)
                    setColorFilter(getColor(R.color.on_surface_secondary))
                    layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                })
            })
        }

    private fun buildTabs(): View {
        tabLayout = TabLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
            addTab(newTab().setText(R.string.timeline))
            addTab(newTab().setText(R.string.plant_journal))
            addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
                override fun onTabSelected(tab: TabLayout.Tab) {
                    val showTimeline = tab.position == 0
                    timelineContainer.visibility = if (showTimeline) View.VISIBLE else View.GONE
                    journalContainer.visibility = if (showTimeline) View.GONE else View.VISIBLE
                }

                override fun onTabUnselected(tab: TabLayout.Tab) {}
                override fun onTabReselected(tab: TabLayout.Tab) {}
            })
        }
        return tabLayout
    }

    private fun renderTimeline(scans: List<ScanEntity>) {
        timelineContainer.removeAllViews()
        if (scans.isEmpty()) {
            emptyScans = emptyState(
                R.drawable.illustration_empty_scans,
                getString(R.string.empty_plant_scans_title),
                getString(R.string.empty_plant_scans_hint),
            )
            timelineContainer.addView(emptyScans)
            return
        }
        scans.forEach { scan -> timelineContainer.addView(scanCard(scan)) }
    }

    private fun renderJournal(entries: List<PlantJournalEntity>) {
        journalContainer.removeAllViews()
        if (entries.isEmpty()) {
            emptyJournal = emptyState(
                R.drawable.illustration_empty_reminders,
                getString(R.string.empty_journal_title),
                getString(R.string.empty_journal_hint),
            )
            journalContainer.addView(emptyJournal)
            return
        }
        entries.forEach { entry -> journalContainer.addView(journalCard(entry)) }
    }

    private fun scanCard(scan: ScanEntity): View {
        val card = baseCard()
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(14.dp())
        }
        row.addView(ImageView(this).apply {
            layoutParams = LinearLayout.LayoutParams(72.dp(), 72.dp())
            scaleType = ImageView.ScaleType.CENTER_CROP
            Glide.with(this@PlantDetailActivity)
                .load(File(scan.imagePath))
                .transform(CenterCrop(), RoundedCorners(16.dp()))
                .placeholder(R.drawable.ic_plant_placeholder)
                .into(this)
            isClickable = true
            setOnClickListener {
                lifecycleScope.launch {
                    val all = plantRepository.getScansForPlant(plantId)
                        .sortedBy { it.timestamp }
                        .map { it.imagePath }
                    val start = all.indexOf(scan.imagePath).coerceAtLeast(0)
                    PhotoViewerActivity.open(this@PlantDetailActivity, all, start)
                }
            }
        })
        val text = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 12.dp() }
        }
        val isRu = LocaleHelper.isRussian(this)
        text.addView(TextView(this).apply {
            this.text = if (isRu) scan.diseaseNameRu else scan.diseaseName
            textSize = 16f
            setTextColor(getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            maxLines = 2
        })
        text.addView(TextView(this).apply {
            this.text = getString(
                R.string.timeline_scan_meta,
                ImageUtils.formatRelativeDate(this@PlantDetailActivity, scan.timestamp),
                (scan.confidence * 100).toInt(),
            )
            textSize = 12f
            setTextColor(getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 5.dp() }
        })
        row.addView(text)
        card.addView(row)
        card.setOnClickListener {
            startActivity(
                Intent(this, ResultActivity::class.java)
                    .putExtra(ResultActivity.EXTRA_SCAN_ID, scan.id)
            )
        }
        return card
    }

    private fun journalCard(entry: PlantJournalEntity): View {
        val card = baseCard()
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.TOP
            setPadding(14.dp())
        }
        row.addView(FrameLayout(this).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            layoutParams = LinearLayout.LayoutParams(46.dp(), 46.dp())
            addView(ImageView(this@PlantDetailActivity).apply {
                setImageResource(
                    when (PlantRepository.JournalType.from(entry.type)) {
                        PlantRepository.JournalType.NOTE -> R.drawable.ic_care
                        PlantRepository.JournalType.PHOTO -> R.drawable.ic_gallery
                        PlantRepository.JournalType.WATERING -> R.drawable.ic_watering
                        PlantRepository.JournalType.FERTILISING -> R.drawable.ic_treatment
                        PlantRepository.JournalType.REPOTTING -> R.drawable.ic_garden
                        PlantRepository.JournalType.PRUNING -> R.drawable.ic_note_pencil
                        PlantRepository.JournalType.MISTING -> R.drawable.ic_warning
                    }
                )
                setColorFilter(getColor(R.color.primary))
                layoutParams = FrameLayout.LayoutParams(24.dp(), 24.dp(), Gravity.CENTER)
            })
        })
        val text = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 12.dp() }
        }
        text.addView(TextView(this).apply {
            this.text = when (PlantRepository.JournalType.from(entry.type)) {
                PlantRepository.JournalType.NOTE -> getString(R.string.note_entry)
                PlantRepository.JournalType.PHOTO -> getString(R.string.photo_entry)
                PlantRepository.JournalType.WATERING -> getString(R.string.watering_entry)
                PlantRepository.JournalType.FERTILISING -> getString(R.string.fertilising_entry)
                PlantRepository.JournalType.REPOTTING -> getString(R.string.repotting_entry)
                PlantRepository.JournalType.PRUNING -> getString(R.string.pruning_entry)
                PlantRepository.JournalType.MISTING -> getString(R.string.misting_entry)
            }
            textSize = 15f
            setTextColor(getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        })
        text.addView(TextView(this).apply {
            this.text = ImageUtils.formatRelativeDate(this@PlantDetailActivity, entry.timestamp)
            textSize = 12f
            setTextColor(getColor(R.color.primary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 2.dp() }
        })
        entry.text?.takeIf { it.isNotBlank() }?.let { body ->
            text.addView(TextView(this).apply {
                this.text = body
                textSize = 14f
                setTextColor(getColor(R.color.on_surface_secondary))
                setLineSpacing(3.dp().toFloat(), 1f)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 7.dp() }
            })
        }
        entry.photoUri?.let { uri ->
            text.addView(ImageView(this).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    160.dp(),
                ).apply { topMargin = 10.dp() }
                scaleType = ImageView.ScaleType.CENTER_CROP
                Glide.with(this@PlantDetailActivity)
                    .load(Uri.parse(uri))
                    .transform(CenterCrop(), RoundedCorners(18.dp()))
                    .into(this)
                isClickable = true
                setOnClickListener { openJournalPhotoViewer(uri) }
            })
        }
        row.addView(text)
        card.addView(row)
        card.isLongClickable = true
        card.setOnLongClickListener { anchor ->
            Haptics.medium(this)
            showJournalContextMenu(anchor, entry)
            true
        }
        return card
    }

    private fun showJournalContextMenu(anchor: View, entry: PlantJournalEntity) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, getString(R.string.edit_entry))
            menu.add(0, 2, 1, getString(R.string.delete_entry))
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> {
                        showEditJournalDialog(entry); true
                    }
                    2 -> {
                        confirmDeleteJournalEntry(entry); true
                    }
                    else -> false
                }
            }
            show()
        }
    }

    private fun showEditJournalDialog(entry: PlantJournalEntity) {
        val type = PlantRepository.JournalType.from(entry.type)
        val hintRes = when (type) {
            PlantRepository.JournalType.NOTE -> R.string.note_hint
            PlantRepository.JournalType.WATERING -> R.string.watering_hint
            PlantRepository.JournalType.PHOTO -> R.string.photo_caption_hint
            PlantRepository.JournalType.FERTILISING -> R.string.fertilising_hint
            PlantRepository.JournalType.REPOTTING -> R.string.repotting_hint
            PlantRepository.JournalType.PRUNING -> R.string.pruning_hint
            PlantRepository.JournalType.MISTING -> R.string.misting_hint
        }
        val allowEmpty = type != PlantRepository.JournalType.NOTE

        val input = TextInputEditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            maxLines = 6
            setText(entry.text.orEmpty())
        }
        val inputLayout = TextInputLayout(this).apply {
            hint = getString(hintRes)
            addView(input)
            setPadding(0, 8.dp(), 0, 0)
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.edit_entry)
            .setView(inputLayout)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .show()
        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val text = input.text?.toString()?.trim().orEmpty()
            if (!allowEmpty && text.isEmpty()) {
                inputLayout.error = getString(R.string.journal_text_required)
                return@setOnClickListener
            }
            lifecycleScope.launch {
                plantRepository.updateJournalEntryText(entry.id, text)
                Toast.makeText(
                    this@PlantDetailActivity,
                    R.string.entry_updated,
                    Toast.LENGTH_SHORT,
                ).show()
                loadPlant()
            }
            dialog.dismiss()
        }
    }

    private fun confirmDeleteJournalEntry(entry: PlantJournalEntity) {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.delete_entry)
            .setMessage(R.string.delete_entry_confirm)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.delete) { _, _ ->
                lifecycleScope.launch {
                    plantRepository.deleteJournalEntry(entry.id)
                    Toast.makeText(
                        this@PlantDetailActivity,
                        R.string.entry_deleted,
                        Toast.LENGTH_SHORT,
                    ).show()
                    loadPlant()
                }
            }
            .show()
    }

    private fun baseCard(): MaterialCardView = MaterialCardView(this).apply {
        radius = 18.dp().toFloat()
        cardElevation = 0f
        strokeWidth = 1.dp()
        strokeColor = getColor(R.color.card_stroke)
        setCardBackgroundColor(getColor(R.color.surface))
        isClickable = true
        foreground = selectable()
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = 12.dp() }
    }

    private fun emptyState(imageRes: Int, title: String, body: String): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(24.dp())
            addView(ImageView(this@PlantDetailActivity).apply {
                setImageResource(imageRes)
                layoutParams = LinearLayout.LayoutParams(170.dp(), 170.dp())
            })
            addView(TextView(this@PlantDetailActivity).apply {
                text = title
                textSize = 20f
                gravity = Gravity.CENTER
                setTextColor(getColor(R.color.on_surface))
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            })
            addView(TextView(this@PlantDetailActivity).apply {
                text = body
                textSize = 14f
                gravity = Gravity.CENTER
                setTextColor(getColor(R.color.on_surface_secondary))
                setLineSpacing(4.dp().toFloat(), 1f)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 8.dp() }
            })
        }

    private fun showNoteDialog() = showTextDialog(
        titleRes = R.string.add_note,
        hintRes = R.string.note_hint,
    ) { text ->
        lifecycleScope.launch {
            plantRepository.addNote(plantId, text)
            loadPlant()
        }
    }

    private fun showWateringDialog() = showTextDialog(
        titleRes = R.string.log_watering,
        hintRes = R.string.watering_hint,
        allowEmpty = true,
    ) { text ->
        lifecycleScope.launch {
            plantRepository.addWatering(plantId, text)
            loadPlant()
        }
    }

    private fun showPhotoCaptionDialog(uri: Uri) = showTextDialog(
        titleRes = R.string.add_photo_log,
        hintRes = R.string.photo_caption_hint,
        allowEmpty = true,
    ) { text ->
        lifecycleScope.launch {
            plantRepository.addPhoto(plantId, uri.toString(), text)
            loadPlant()
        }
    }

    private fun showTextDialog(
        titleRes: Int,
        hintRes: Int,
        allowEmpty: Boolean = false,
        onSave: (String) -> Unit,
    ) {
        val input = TextInputEditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            maxLines = 6
        }
        val inputLayout = TextInputLayout(this).apply {
            hint = getString(hintRes)
            addView(input)
            setPadding(0, 8.dp(), 0, 0)
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(titleRes)
            .setView(inputLayout)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .show()
        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val text = input.text?.toString()?.trim().orEmpty()
            if (!allowEmpty && text.isEmpty()) {
                inputLayout.error = getString(R.string.journal_text_required)
                return@setOnClickListener
            }
            onSave(text)
            dialog.dismiss()
        }
    }

    private fun selectable(): android.graphics.drawable.Drawable? {
        val outValue = TypedValue()
        theme.resolveAttribute(android.R.attr.selectableItemBackground, outValue, true)
        return getDrawable(outValue.resourceId)
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()

    /**
     * Disease-progression card. Builds a history list from the plant's
     * scan entities (oldest first, capped to the most recent 12 to keep
     * the request body bounded) and asks `/api/progression` for the
     * 5-point severity sparkline.
     *
     * On a plant with zero or one scan we render a "no history yet"
     * placeholder without hitting the network — the endpoint would
     * return ``trend="baseline"`` anyway.
     */
    private fun buildProgressionCard(scans: List<ScanEntity>): View {
        val card = MaterialCardView(this).apply {
            radius = 22.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
        }
        val title = TextView(this).apply {
            text = getString(R.string.progression_title)
            textSize = 15f
            setTextColor(getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        }
        val trendChip = TextView(this).apply {
            textSize = 12f
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            setPadding(10.dp(), 4.dp(), 10.dp(), 4.dp())
            background = getDrawable(R.drawable.bg_plant_stat_pill)
        }
        val titleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(trendChip)
        }
        body.addView(titleRow)

        val sparkline = SparklineView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                64.dp(),
            ).apply { topMargin = 10.dp() }
        }
        body.addView(sparkline)

        val meta = TextView(this).apply {
            textSize = 12f
            setTextColor(getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
        body.addView(meta)
        card.addView(body)

        if (scans.size < 2) {
            // Single-scan plants get a baseline placeholder + a CTA to
            // book a follow-up scan in 7 days. The CTA wires straight
            // into the `check` reminder type — accepting it both
            // confirms the schedule and removes the empty-state hint
            // for next time.
            trendChip.text = getString(R.string.progression_trend_baseline)
            trendChip.setTextColor(getColor(R.color.on_surface_secondary))
            sparkline.setData(emptyList(), "baseline")
            meta.text = if (scans.isEmpty()) ""
                else getString(
                    R.string.progression_history_meta,
                    scans.size,
                    formatRelativeDate(this, scans[0].timestamp),
                )
            if (scans.isNotEmpty()) body.addView(buildProgressionCTA())
            return card
        }

        // Loading state.
        trendChip.text = getString(R.string.progression_loading)
        trendChip.setTextColor(getColor(R.color.on_surface_secondary))
        meta.text = ""

        val app = application as PlantDiseasesApp
        // Server expects oldest-first; cap to the 12 newest to keep
        // payloads bounded (Pydantic on the server doesn't, but the
        // device's network stack still has to upload them).
        val ordered = scans.sortedBy { it.timestamp }.takeLast(12)
        val historyPayload = ordered.map { app.scanRepository.scanToProgressionPayload(it) }
        val newest = ordered.last()
        lifecycleScope.launch {
            val res = app.scanRepository.fetchProgression(
                ProgressionRequest(history = historyPayload),
            )
            res.onSuccess { resp ->
                val trendLabel = when (resp.trend) {
                    "improving" -> getString(R.string.progression_trend_improving)
                    "worsening" -> getString(R.string.progression_trend_worsening)
                    "stable" -> getString(R.string.progression_trend_stable)
                    else -> getString(R.string.progression_trend_baseline)
                }
                val trendColor = when (resp.trend) {
                    "improving" -> getColor(R.color.healthy_green)
                    "worsening" -> getColor(R.color.disease_red)
                    "stable" -> getColor(R.color.neutral_blue)
                    else -> getColor(R.color.on_surface_secondary)
                }
                trendChip.text = trendLabel
                trendChip.setTextColor(trendColor)
                sparkline.setData(resp.sparkline ?: emptyList(), resp.trend)
                meta.text = getString(
                    R.string.progression_history_meta,
                    resp.historyLength ?: ordered.size,
                    formatRelativeDate(this@PlantDetailActivity, newest.timestamp),
                )
            }.onFailure {
                trendChip.text = getString(R.string.progression_trend_baseline)
                trendChip.setTextColor(getColor(R.color.on_surface_secondary))
                meta.text = getString(R.string.progression_failed)
                sparkline.setData(emptyList(), "baseline")
            }
        }
        return card
    }

    /** Builds the "Take a follow-up scan in 7 days" CTA shown when
     *  the plant has exactly one scan so far — the sparkline has
     *  nothing to draw, but we still want a forward-looking action
     *  instead of a dead end. Wires into the existing `check`
     *  reminder type via [ReminderManager]. */
    private fun buildProgressionCTA(): View {
        val ctx = this
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.bg_plant_stat_pill)
            setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }
        container.addView(TextView(ctx).apply {
            text = getString(R.string.progression_followup_cta)
            textSize = 13f
            setTextColor(getColor(R.color.on_surface))
            setLineSpacing(2.dp().toFloat(), 1f)
        })
        container.addView(com.google.android.material.button.MaterialButton(
            ctx, null, com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = getString(R.string.progression_followup_remind)
            cornerRadius = 12.dp()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
            setOnClickListener {
                Haptics.tick(this@PlantDetailActivity)
                lifecycleScope.launch {
                    val plant = currentPlant ?: return@launch
                    val reminder = com.plantdiseases.app.data.local.PlantReminderEntity(
                        plantId = plant.id,
                        type = "check",
                        intervalDays = 7,
                        hour = 9,
                        minute = 0,
                        enabled = true,
                    )
                    plantRepository.saveReminder(reminder)
                    com.plantdiseases.app.util.ReminderManager.schedule(
                        context = this@PlantDetailActivity,
                        plantId = plant.id,
                        plantName = plant.name,
                        type = reminder.type,
                        intervalDays = reminder.intervalDays,
                        hour = reminder.hour,
                        minute = reminder.minute,
                    )
                    Toast.makeText(
                        this@PlantDetailActivity,
                        R.string.progression_followup_scheduled,
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
        })
        return container
    }

    /** Open the full-screen photo viewer rooted at a journal photo entry.
     *  Builds the viewer's chronological list from every PHOTO entry on
     *  this plant so the user can swipe between them. */
    private fun openJournalPhotoViewer(tappedUri: String) {
        lifecycleScope.launch {
            val photos = plantRepository.getJournalEntries(plantId)
                .filter { it.type == PlantRepository.JournalType.PHOTO.value }
                .mapNotNull { it.photoUri }
                .sorted() // approximates chrono order; the journal-render
                          // function already returns newest-first
            val ordered = photos.distinct()
            val start = ordered.indexOf(tappedUri).coerceAtLeast(0)
            PhotoViewerActivity.open(this@PlantDetailActivity, ordered, start)
        }
    }

    /** Open the viewer on the hero cover (the cover-photo if set, else
     *  the most recent scan). When the plant has multiple scans we
     *  hand the viewer the whole scan image list so the user can
     *  swipe through them as a growth gallery. */
    private fun openHeroPhotoViewer(plant: PlantEntity, scans: List<ScanEntity>) {
        if (scans.isEmpty() && plant.coverPhotoUri == null) return
        val scanImages = scans.sortedBy { it.timestamp }.map { it.imagePath }
        val ordered = listOfNotNull(plant.coverPhotoUri) +
            scanImages.filter { it != plant.coverPhotoUri }
        val deduped = ordered.distinct()
        if (deduped.isEmpty()) return
        PhotoViewerActivity.open(this, deduped, 0)
    }

    companion object {
        const val EXTRA_PLANT_ID = "extra_plant_id"
    }
}
