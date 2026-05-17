package com.plantdiseases.app.ui.camera

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.widget.NestedScrollView
import com.google.android.material.appbar.AppBarLayout
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.card.MaterialCardView
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.plantdiseases.app.R
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import kotlin.math.ln
import kotlin.math.pow
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class LightMeterActivity : AppCompatActivity(), SensorEventListener {

    private lateinit var tvLux: TextView
    private lateinit var tvStatus: TextView
    private lateinit var progressLight: LinearProgressIndicator
    private lateinit var tvSpeciesIdeal: TextView
    private lateinit var tvSpeciesStatus: TextView
    private lateinit var tipCard: MaterialCardView
    private lateinit var tvTip: TextView
    private lateinit var chipGroup: ChipGroup
    private var previewView: PreviewView? = null

    private var sensorManager: SensorManager? = null
    private var lightSensor: Sensor? = null
    private var smoothedLux = 0f
    private var hasSensor = true
    private var cameraProvider: ProcessCameraProvider? = null
    private lateinit var cameraExecutor: ExecutorService
    private var selectedSpecies = SPECIES[0]
    private var lastCameraSampleAt = 0L
    // Session peak — the highest reading since onCreate. Used both as
    // a "remember the brightest spot" affordance for the user and as
    // an auto-expand hint when the reading goes off the 30 k cap.
    private var peakLux = 0f
    private var contextPlantId: Long = -1L

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startCameraFallback()
        } else {
            tvStatus.text = getString(R.string.camera_permission_needed)
            tvStatus.setTextColor(getColor(R.color.disease_red))
        }
    }

    data class SpeciesInfo(val labelRes: Int, val minLux: Int, val maxLux: Int)

    companion object {
        /** Pass the plant's PlantNet scientific name in this extra to
         *  pre-select the matching species bucket on launch. Without
         *  it the gauge defaults to "general" — fine for unknown
         *  plants, wrong for succulents (need 5-15k) or ferns
         *  (need 200-1k). */
        const val EXTRA_SCIENTIFIC_NAME = "extra_scientific_name"
        /** Pass the plant's row id (>0) so the session's peak lux
         *  gets recorded back onto the plant on close. Without it
         *  the meter behaves as a pure standalone tool — no
         *  persistence. */
        const val EXTRA_PLANT_ID = "extra_plant_id"

        private const val IDX_GENERAL = 0
        private const val IDX_SUCCULENT = 1
        private const val IDX_TROPICAL = 2
        private const val IDX_FERN = 3
        private const val IDX_VEGETABLE = 4

        private val SPECIES = listOf(
            SpeciesInfo(R.string.light_species_general, 1000, 5000),
            SpeciesInfo(R.string.light_species_succulent, 5000, 15000),
            SpeciesInfo(R.string.light_species_tropical, 500, 3000),
            SpeciesInfo(R.string.light_species_fern, 200, 1000),
            SpeciesInfo(R.string.light_species_vegetable, 5000, 10000),
        )

        /** Heuristic genus→bucket mapping. Lower-cases the genus
         *  (first whitespace-separated token of the binomial) and
         *  routes the popular indoor genera into the right bucket;
         *  anything else falls through to "general". Adding more
         *  genera here is the right place to grow coverage. */
        @JvmStatic
        fun pickSpeciesBucket(scientificName: String): Int {
            val genus = scientificName.trim().substringBefore(' ').lowercase()
            return when (genus) {
                // Common indoor succulents.
                "echeveria", "sedum", "crassula", "aloe", "haworthia",
                "kalanchoe", "sansevieria", "agave", "lithops",
                "euphorbia" -> IDX_SUCCULENT
                // Tropical aroids + foliage favourites.
                "monstera", "philodendron", "epipremnum", "pothos",
                "calathea", "maranta", "spathiphyllum", "anthurium",
                "ficus", "syngonium", "alocasia", "dieffenbachia",
                "dracaena", "schefflera", "begonia", "peperomia",
                "saintpaulia" -> IDX_TROPICAL
                // Ferns.
                "nephrolepis", "adiantum", "asplenium", "platycerium",
                "pteris", "davallia", "polystichum" -> IDX_FERN
                // Edible / vegetable seedlings often started indoors.
                "solanum", "capsicum", "lactuca", "ocimum", "mentha",
                "petroselinum" -> IDX_VEGETABLE
                else -> IDX_GENERAL
            }
        }

        // Houseplant-relevant range. Above 30k lux you're outdoors in
        // direct sun and the species bands stop being useful — capping
        // the gauge here gives indoor readings (10..2000 lux) most of
        // the bar instead of huddling against the left edge.
        private const val MAX_LUX_DISPLAY = 30_000f
        // Log-scale base for the adaptive gauge. Indoor lighting spans
        // ~3 orders of magnitude (10..10k); mapping it linearly into a
        // 0..30k bar wastes 90% of the bar on outdoor brightness no
        // houseplant ever sees. ln-space gives every order of magnitude
        // equal real estate so the bar visibly responds to lamp / blind
        // changes that matter for plant care.
        private const val PROGRESS_RESOLUTION = 1000
        private const val SMOOTHING_ALPHA = 0.1f
        // Major ticks (lux) shown under the gauge. Ends pinned to 1 and
        // MAX_LUX_DISPLAY so the labels line up with the bar extents.
        private val SCALE_TICKS = intArrayOf(1, 10, 100, 1_000, 10_000, 30_000)
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        cameraExecutor = Executors.newSingleThreadExecutor()
        // Pre-select the species bucket based on the launching plant's
        // scientific name. Without this, every plant lands on "general"
        // (1000-5000 lux) which is wrong for both succulents (which
        // want 5-15k) and ferns (which want 200-1000).
        intent.getStringExtra(EXTRA_SCIENTIFIC_NAME)?.let { sci ->
            val pick = pickSpeciesBucket(sci)
            if (pick in SPECIES.indices) selectedSpecies = SPECIES[pick]
        }
        contextPlantId = intent.getLongExtra(EXTRA_PLANT_ID, -1L)

        val root = androidx.coordinatorlayout.widget.CoordinatorLayout(this).apply {
            setBackgroundColor(getColor(R.color.background))
        }

        // AppBar
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
        val toolbar = MaterialToolbar(this).apply {
            layoutParams = AppBarLayout.LayoutParams(
                AppBarLayout.LayoutParams.MATCH_PARENT,
                64.dp(),
            )
            setContentInsetStartWithNavigation(0)
            setContentInsetsAbsolute(0, 0)
            setContentInsetsRelative(0, 0)
            setPadding(16.dp(), 0, 16.dp(), 0)
            title = getString(R.string.light_meter)
            setNavigationIcon(R.drawable.ic_back)
            setNavigationIconTint(getColor(R.color.toolbar_title))
            setTitleTextColor(getColor(R.color.toolbar_title))
            setNavigationOnClickListener { finish() }
        }
        appBar.addView(toolbar)
        root.addView(appBar)

        // Scrollable content
        val scroll = NestedScrollView(this).apply {
            layoutParams = androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams(
                androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams.MATCH_PARENT,
                androidx.coordinatorlayout.widget.CoordinatorLayout.LayoutParams.MATCH_PARENT,
            ).apply { behavior = AppBarLayout.ScrollingViewBehavior() }
            clipToPadding = false
        }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 16.dp(), 16.dp(), 28.dp())
        }
        scroll.addView(content)
        root.addView(scroll)
        setContentView(root)

        // Gauge card
        content.addView(buildGaugeCard())

        // Species chips
        content.addView(buildSpeciesSection())

        // Tips
        content.addView(buildTipsCard())

        // Sensor setup
        sensorManager = getSystemService(SENSOR_SERVICE) as SensorManager
        lightSensor = sensorManager?.getDefaultSensor(Sensor.TYPE_LIGHT)
        if (lightSensor == null) {
            hasSensor = false
            showNoSensorMessage(content)
            ensureCameraFallback()
        }
    }

    private fun buildGaugeCard(): View {
        val card = MaterialCardView(this).apply {
            radius = 24.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 16.dp() }
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(24.dp(), 28.dp(), 24.dp(), 24.dp())
        }

        // Big lux number
        tvLux = TextView(this).apply {
            text = getString(R.string.light_measuring)
            textSize = 64f
            setTextColor(getColor(R.color.primary))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            gravity = Gravity.CENTER
        }
        col.addView(tvLux)

        col.addView(TextView(this).apply {
            text = getString(R.string.light_lux)
            textSize = 18f
            setTextColor(getColor(R.color.on_surface_secondary))
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = (-4).dp() }
        })

        tvStatus = TextView(this).apply {
            text = ""
            textSize = 16f
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
        col.addView(tvStatus)

        // Progress bar: adaptive log scale (see luxToProgress). Indoor
        // levels (10..2000 lux) sit comfortably in the middle of the
        // bar instead of hugging the left edge.
        progressLight = LinearProgressIndicator(this).apply {
            max = PROGRESS_RESOLUTION
            progress = 0
            trackCornerRadius = 6.dp()
            trackThickness = 12.dp()
            setIndicatorColor(getColor(R.color.primary))
            trackColor = getColor(R.color.card_stroke)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 16.dp() }
        }
        col.addView(progressLight)

        // Tick row: each tick label is laid out at the same log
        // position the bar uses, so reading the label off the bar
        // gives the right lux value.
        col.addView(buildScaleRow())

        card.addView(col)
        return card
    }

    private fun buildScaleRow(): View {
        val row = FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        }
        // We can't know the exact bar width here, so each tick is
        // positioned by horizontal bias via a LayoutParams trick:
        // assign each label a weight inside a LinearLayout that mirrors
        // the bar's log spacing. The first label is left-aligned, the
        // last right-aligned, and middle labels get translated via
        // post-layout positioning relative to the bar width.
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        SCALE_TICKS.forEachIndexed { idx, lux ->
            val pos = luxToFraction(lux.toFloat())
            val prevPos = if (idx == 0) 0f else luxToFraction(SCALE_TICKS[idx - 1].toFloat())
            // The gap before each label is proportional to log-distance
            // from the previous tick. Using weights gives us a
            // resolution-independent placement.
            val gapWeight = (pos - prevPos).coerceAtLeast(0f)
            if (gapWeight > 0f) {
                bar.addView(View(this).apply {
                    layoutParams = LinearLayout.LayoutParams(0, 1, gapWeight)
                })
            }
            bar.addView(TextView(this).apply {
                text = formatLuxLabel(lux)
                textSize = 10f
                setTextColor(getColor(R.color.on_surface_secondary))
            })
        }
        row.addView(bar)
        return row
    }

    private fun formatLuxLabel(lux: Int): String = when {
        lux >= 1000 -> "${lux / 1000}k"
        else -> lux.toString()
    }

    private fun buildSpeciesSection(): View {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 16.dp() }
        }

        col.addView(TextView(this).apply {
            text = getString(R.string.light_plant_type)
            textSize = 14f
            setTextColor(getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 8.dp() }
        })

        // Horizontal chip group
        val chipScroll = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
        }
        chipGroup = ChipGroup(this).apply {
            isSingleSelection = true
        }
        val initialIndex = SPECIES.indexOf(selectedSpecies).coerceAtLeast(0)
        SPECIES.forEachIndexed { i, species ->
            val chip = Chip(this).apply {
                text = getString(species.labelRes)
                isCheckable = true
                isChecked = (i == initialIndex)
                layoutParams = ChipGroup.LayoutParams(
                    ChipGroup.LayoutParams.WRAP_CONTENT,
                    ChipGroup.LayoutParams.WRAP_CONTENT,
                )
                setOnCheckedChangeListener { _, checked ->
                    if (checked) {
                        selectedSpecies = species
                        updateDisplay(smoothedLux)
                    }
                }
            }
            chipGroup.addView(chip)
        }
        chipScroll.addView(chipGroup)
        col.addView(chipScroll)

        // Species ideal card
        val idealCard = MaterialCardView(this).apply {
            radius = 16.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
        }
        val idealContent = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
        }
        tvSpeciesIdeal = TextView(this).apply {
            text = getString(
                R.string.light_ideal_range,
                getString(SPECIES[0].labelRes),
                SPECIES[0].minLux,
                SPECIES[0].maxLux,
            )
            textSize = 15f
            setTextColor(getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        }
        idealContent.addView(tvSpeciesIdeal)
        tvSpeciesStatus = TextView(this).apply {
            text = ""
            textSize = 13f
            setTextColor(getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        }
        idealContent.addView(tvSpeciesStatus)
        idealCard.addView(idealContent)
        col.addView(idealCard)

        return col
    }

    private fun buildTipsCard(): View {
        tipCard = MaterialCardView(this).apply {
            radius = 16.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = 64.dp()
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
        }
        col.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_light)
            setColorFilter(getColor(R.color.primary))
            layoutParams = LinearLayout.LayoutParams(24.dp(), 24.dp())
        })
        tvTip = TextView(this).apply {
            text = getString(R.string.light_tip_ideal)
            textSize = 14f
            setTextColor(getColor(R.color.on_surface))
            setLineSpacing(3.dp().toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 12.dp() }
        }
        col.addView(tvTip)
        tipCard.addView(col)
        return tipCard
    }

    private fun showNoSensorMessage(parent: LinearLayout) {
        parent.addView(
            MaterialCardView(this).apply {
                radius = 16.dp().toFloat()
                cardElevation = 0f
                strokeWidth = 1.dp()
                strokeColor = getColor(R.color.card_stroke)
                setCardBackgroundColor(getColor(R.color.surface))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 12.dp() }
                addView(TextView(this@LightMeterActivity).apply {
                    text = getString(R.string.light_no_sensor)
                    textSize = 14f
                    setTextColor(getColor(R.color.on_surface_secondary))
                    gravity = Gravity.CENTER
                    setPadding(20.dp(), 20.dp(), 20.dp(), 20.dp())
                })
            }
        )
        previewView = PreviewView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                180.dp(),
            ).apply { topMargin = 12.dp() }
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }
        parent.addView(
            MaterialCardView(this).apply {
                radius = 18.dp().toFloat()
                cardElevation = 0f
                strokeWidth = 1.dp()
                strokeColor = getColor(R.color.card_stroke)
                setCardBackgroundColor(getColor(R.color.surface))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 12.dp() }
                addView(previewView)
            }
        )
    }

    private fun ensureCameraFallback() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCameraFallback()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun startCameraFallback() {
        val previewSurface = previewView ?: return
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            val provider = try {
                providerFuture.get()
            } catch (_: Exception) {
                tvStatus.text = getString(R.string.camera_error)
                tvStatus.setTextColor(getColor(R.color.disease_red))
                return@addListener
            }
            cameraProvider = provider

            val preview = Preview.Builder().build().apply {
                setSurfaceProvider(previewSurface.surfaceProvider)
            }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
                .apply {
                    setAnalyzer(cameraExecutor) { image ->
                        analyzeCameraLuminance(image)
                    }
                }

            try {
                provider.unbindAll()
                provider.bindToLifecycle(
                    this,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                )
            } catch (_: Exception) {
                tvStatus.text = getString(R.string.camera_error)
                tvStatus.setTextColor(getColor(R.color.disease_red))
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyzeCameraLuminance(image: ImageProxy) {
        try {
            val now = System.currentTimeMillis()
            if (now - lastCameraSampleAt < 450L) return
            lastCameraSampleAt = now

            val buffer = image.planes.firstOrNull()?.buffer ?: return
            val remaining = buffer.remaining()
            if (remaining <= 0) return

            var sum = 0L
            var samples = 0
            val step = (remaining / 1000).coerceAtLeast(1)
            for (i in 0 until remaining step step) {
                sum += buffer.get(i).toInt() and 0xFF
                samples++
            }
            val avgLuma = if (samples == 0) 0f else sum.toFloat() / samples
            val estimatedLux = estimateLuxFromLuma(avgLuma)
            smoothedLux = if (smoothedLux == 0f) estimatedLux
            else SMOOTHING_ALPHA * estimatedLux + (1f - SMOOTHING_ALPHA) * smoothedLux
            runOnUiThread { updateDisplay(smoothedLux) }
        } finally {
            image.close()
        }
    }

    private fun estimateLuxFromLuma(luma: Float): Float {
        // 8-bit luma is not a calibrated lux signal — the camera applies
        // its own auto-exposure compensation, so the same scene at very
        // different brightness levels can yield similar luma values.
        // We map the normalized luma through a 10^x curve that covers
        // the 5..30k useful range, then clamp to the displayable cap
        // so the bar and the number can't disagree.
        val normalized = (luma / 255f).coerceIn(0.02f, 1f)
        return (10f.pow(normalized * 4.5f) - 1f).coerceIn(5f, MAX_LUX_DISPLAY)
    }

    /**
     * Log-scaled position in [0, 1] for the given lux reading. ln(1+x)
     * lets 0 lux map to 0 cleanly without singularities, and gives
     * each decade of light (10x, 100x, 1000x) roughly equal width on
     * the bar — which matches how plants perceive light.
     */
    private fun luxToFraction(lux: Float): Float {
        val clamped = lux.coerceIn(0f, MAX_LUX_DISPLAY)
        val numerator = ln(1f + clamped)
        val denominator = ln(1f + MAX_LUX_DISPLAY)
        return (numerator / denominator).coerceIn(0f, 1f)
    }

    private fun updateDisplay(lux: Float) {
        if (lux > peakLux) peakLux = lux
        val luxInt = lux.toInt()
        tvLux.text = if (luxInt > 999) "%,d".format(luxInt) else luxInt.toString()

        val (statusText, statusColor) = when {
            lux < selectedSpecies.minLux -> getString(R.string.light_too_dark) to getColor(R.color.disease_red)
            lux <= selectedSpecies.maxLux -> getString(R.string.light_ideal) to getColor(R.color.primary)
            else -> getString(R.string.light_too_bright) to getColor(R.color.warning_amber)
        }
        tvStatus.text = statusText
        tvStatus.setTextColor(statusColor)

        progressLight.progress = (luxToFraction(lux) * PROGRESS_RESOLUTION)
            .toInt().coerceIn(0, PROGRESS_RESOLUTION)

        updateSpeciesDisplay()
        updateTip(lux)
    }

    private fun updateSpeciesDisplay() {
        // Show session-peak alongside the species ideal range so the
        // user can sweep the phone around the room and remember the
        // brightest spot without having to watch the live number.
        val peakInt = peakLux.toInt()
        tvSpeciesIdeal.text = getString(
            R.string.light_ideal_range_with_peak,
            getString(selectedSpecies.labelRes),
            selectedSpecies.minLux,
            selectedSpecies.maxLux,
            peakInt,
        )
        val currentLux = smoothedLux.toInt()
        tvSpeciesStatus.text = when {
            currentLux < selectedSpecies.minLux -> getString(R.string.light_too_dark)
            currentLux > selectedSpecies.maxLux -> getString(R.string.light_too_bright)
            else -> getString(R.string.light_ideal)
        }
    }

    private fun updateTip(lux: Float) {
        tvTip.text = when {
            lux < selectedSpecies.minLux -> getString(R.string.light_tip_dark)
            lux <= selectedSpecies.maxLux -> getString(R.string.light_tip_ideal)
            else -> getString(R.string.light_tip_bright)
        }
    }

    override fun onResume() {
        super.onResume()
        if (hasSensor) {
            sensorManager?.registerListener(this, lightSensor, SensorManager.SENSOR_DELAY_UI)
        } else if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCameraFallback()
        }
    }

    override fun onPause() {
        super.onPause()
        sensorManager?.unregisterListener(this)
        cameraProvider?.unbindAll()
        // Persist the session peak back to the launching plant so
        // the Care Plan card can show "measured here: N lx · 3 days
        // ago". onPause runs reliably for both back-button and
        // home-button exits; doing it on a click would miss the
        // common "user just navigates away" exit.
        val plantId = contextPlantId
        val peak = peakLux.toInt()
        if (plantId > 0 && peak > 0) {
            val app = applicationContext as com.plantdiseases.app.PlantDiseasesApp
            // Fire-and-forget — by the time onPause returns the
            // activity is gone, so we can't await without leaking
            // a coroutine into a stopped lifecycle.
            kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
                app.plantRepository.updateMeasuredLux(plantId, peak)
            }
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type == Sensor.TYPE_LIGHT) {
            val raw = event.values[0]
            smoothedLux = if (smoothedLux == 0f) raw
            else SMOOTHING_ALPHA * raw + (1f - SMOOTHING_ALPHA) * smoothedLux
            updateDisplay(smoothedLux)
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        cameraProvider?.unbindAll()
        if (::cameraExecutor.isInitialized) {
            cameraExecutor.shutdown()
        }
        super.onDestroy()
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics
    ).toInt()
}
