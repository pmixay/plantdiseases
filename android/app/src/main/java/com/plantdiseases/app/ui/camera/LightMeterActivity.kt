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
        private val SPECIES = listOf(
            SpeciesInfo(R.string.light_species_general, 1000, 5000),
            SpeciesInfo(R.string.light_species_succulent, 5000, 15000),
            SpeciesInfo(R.string.light_species_tropical, 500, 3000),
            SpeciesInfo(R.string.light_species_fern, 200, 1000),
            SpeciesInfo(R.string.light_species_vegetable, 5000, 10000),
        )
        private const val MAX_LUX_DISPLAY = 100_000f
        private const val SMOOTHING_ALPHA = 0.1f
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        cameraExecutor = Executors.newSingleThreadExecutor()

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

        // Progress bar: linear scale 0..100000 lux
        progressLight = LinearProgressIndicator(this).apply {
            max = 1000
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

        val scaleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        }
        scaleRow.addView(TextView(this).apply {
            text = "0"
            textSize = 10f
            setTextColor(getColor(R.color.on_surface_secondary))
        })
        scaleRow.addView(View(this).apply {
            layoutParams = LinearLayout.LayoutParams(0, 1, 1f)
        })
        scaleRow.addView(TextView(this).apply {
            text = "100k lux"
            textSize = 10f
            setTextColor(getColor(R.color.on_surface_secondary))
        })
        col.addView(scaleRow)

        card.addView(col)
        return card
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
        SPECIES.forEachIndexed { i, species ->
            val chip = Chip(this).apply {
                text = getString(species.labelRes)
                isCheckable = true
                isChecked = (i == 0)
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
        val normalized = (luma / 255f).coerceIn(0.02f, 1f)
        return (10f.pow(normalized * 4.7f) - 1f).coerceIn(5f, 50_000f)
    }

    private fun updateDisplay(lux: Float) {
        val luxInt = lux.toInt()
        tvLux.text = if (luxInt > 999) "%,d".format(luxInt) else luxInt.toString()

        val (statusText, statusColor) = when {
            lux < selectedSpecies.minLux -> getString(R.string.light_too_dark) to getColor(R.color.disease_red)
            lux <= selectedSpecies.maxLux -> getString(R.string.light_ideal) to getColor(R.color.primary)
            else -> getString(R.string.light_too_bright) to getColor(R.color.warning_amber)
        }
        tvStatus.text = statusText
        tvStatus.setTextColor(statusColor)

        // Linear progress so the bar position matches the labelled scale
        // (0 → empty, 100k lux → full). Indoor readings sit toward the
        // left third, which is the truthful picture of how much usable
        // light a houseplant gets.
        val linearProgress = (lux.coerceAtLeast(0f) / MAX_LUX_DISPLAY * 1000)
            .toInt().coerceIn(0, 1000)
        progressLight.progress = linearProgress

        updateSpeciesDisplay()
        updateTip(lux)
    }

    private fun updateSpeciesDisplay() {
        tvSpeciesIdeal.text = getString(
            R.string.light_ideal_range,
            getString(selectedSpecies.labelRes),
            selectedSpecies.minLux,
            selectedSpecies.maxLux,
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
