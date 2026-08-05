package com.plantdiseases.app.ui.camera

import android.content.Context
import android.os.Bundle
import android.util.TypedValue
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.google.android.material.chip.Chip
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityWaterCalculatorBinding
import com.plantdiseases.app.util.LocaleHelper
import java.util.Calendar
import kotlin.math.PI
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Water Calculator — an advisory tool that estimates how much water (ml) and
 * how often (days) a plant needs, from four inputs the user can adjust: pot
 * diameter, plant type, the light at its spot, and the season. The plant type
 * is auto-detected from the launching plant's species (same genus→bucket
 * mapping the Light Meter uses) but stays user-overridable.
 *
 * Everything is a transparent heuristic ([computeWatering]); there is no
 * sensor and no network. It mirrors the Light Meter's standalone-tool shape:
 * a gradient toolbar over a scrollable column of cards.
 */
class WaterCalculatorActivity : AppCompatActivity() {

    private lateinit var binding: ActivityWaterCalculatorBinding

    private var potDiameterCm = DEFAULT_POT_CM
    private var typeIndex = TYPE_GENERAL
    private var lightLevel = LIGHT_MEDIUM
    private var dormant = false

    companion object {
        /** Pass the plant's scientific (Latin) name so the plant-type chip
         *  pre-selects the right bucket. */
        const val EXTRA_SCIENTIFIC_NAME = "extra_scientific_name"

        /** Optional common / free-text name — auto-detection fallback. */
        const val EXTRA_COMMON_NAME = "extra_common_name"

        // Plant-type indices. Deliberately identical in order to the Light
        // Meter's SPECIES table so LightMeterActivity.pickSpeciesBucket()
        // returns a value usable directly as an index here.
        private const val TYPE_GENERAL = 0
        private const val TYPE_SUCCULENT = 1
        private const val TYPE_TROPICAL = 2
        private const val TYPE_FERN = 3
        private const val TYPE_VEGETABLE = 4

        private const val LIGHT_LOW = 0
        private const val LIGHT_MEDIUM = 1
        private const val LIGHT_BRIGHT = 2

        private const val MIN_POT_CM = 6
        private const val MAX_POT_CM = 40
        private const val DEFAULT_POT_CM = 15

        // Pot height is taken as a fraction of its diameter — typical
        // houseplant pots are a little shorter than they are wide.
        private const val POT_HEIGHT_FACTOR = 0.7

        // Fraction of the soil's volume delivered in a single thorough
        // watering, per plant type. Succulents get a light drink; ferns the
        // most. These are what map a pot's soil volume → millilitres.
        private val WATER_FRACTION = doubleArrayOf(
            0.15, // general
            0.08, // succulent
            0.18, // tropical
            0.22, // fern
            0.20, // vegetable
        )

        // Baseline days between waterings for a 15 cm pot in medium light
        // during the growing season, per plant type.
        private val BASE_DAYS = doubleArrayOf(
            7.0, // general
            14.0, // succulent
            6.0, // tropical
            4.0, // fern
            3.0, // vegetable
        )

        private val LIGHT_FACTOR = doubleArrayOf(1.3, 1.0, 0.8) // low / medium / bright

        /**
         * Pure watering heuristic. Returns (millilitres per watering,
         * interval in days). Extracted as a companion function with no Android
         * dependencies so it is unit-testable.
         *
         * @param potDiameterCm pot diameter in centimetres
         * @param typeIndex one of TYPE_* (0..4)
         * @param lightLevel one of LIGHT_* (0 low, 1 medium, 2 bright)
         * @param dormant true in the autumn/winter rest period
         */
        fun computeWatering(
            potDiameterCm: Int,
            typeIndex: Int,
            lightLevel: Int,
            dormant: Boolean,
        ): Pair<Int, Int> {
            val d = potDiameterCm.coerceIn(MIN_POT_CM, MAX_POT_CM).toDouble()
            val type = typeIndex.coerceIn(0, WATER_FRACTION.size - 1)
            val light = lightLevel.coerceIn(0, LIGHT_FACTOR.size - 1)

            // Soil volume of a cylinder ≈ π r² h, in cm³ (= ml).
            val radius = d / 2.0
            val height = d * POT_HEIGHT_FACTOR
            val soilMl = PI * radius * radius * height

            val ml = (soilMl * WATER_FRACTION[type])
                .roundToInt()
                .let { (it + 5) / 10 * 10 } // round to nearest 10 ml
                .coerceIn(30, 2000)

            // Bigger pots hold more water and dry slower → longer interval.
            val sizeFactor = sqrt(d / DEFAULT_POT_CM)
            val seasonFactor = if (dormant) 1.5 else 1.0
            val days = (BASE_DAYS[type] * LIGHT_FACTOR[light] * seasonFactor * sizeFactor)
                .roundToInt()
                .coerceIn(1, 30)

            return ml to days
        }

        /** Northern-hemisphere default: treat Oct–Feb as the dormant rest
         *  period. The user can flip the Season chips either way. */
        private fun defaultDormant(month0: Int): Boolean = month0 >= Calendar.OCTOBER ||
            month0 <= Calendar.FEBRUARY
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Auto-detect the plant type from the launching plant's species —
        // scientific name first, then the common/free-text name.
        val sci = intent.getStringExtra(EXTRA_SCIENTIFIC_NAME).orEmpty()
        var pick = LightMeterActivity.pickSpeciesBucket(sci)
        if (pick == TYPE_GENERAL) {
            val common = intent.getStringExtra(EXTRA_COMMON_NAME).orEmpty()
            if (common.isNotBlank()) pick = LightMeterActivity.pickSpeciesBucket(common)
        }
        typeIndex = pick.coerceIn(0, 4)
        dormant = defaultDormant(Calendar.getInstance().get(Calendar.MONTH))

        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityWaterCalculatorBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.waterScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.waterAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.waterScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.waterToolbar.apply {
            setContentInsetStartWithNavigation(0)
            setContentInsetsAbsolute(0, 0)
            setContentInsetsRelative(0, 0)
            setPadding(16.dp(), 0, 16.dp(), 0)
            setNavigationIconTint(getColor(R.color.toolbar_title))
            setNavigationOnClickListener { finish() }
        }

        setupTypeChips()
        setupLightChips()
        setupSeasonChips()
        setupPotSlider()

        recompute()
    }

    private fun setupPotSlider() {
        binding.potSlider.apply {
            valueFrom = MIN_POT_CM.toFloat()
            valueTo = MAX_POT_CM.toFloat()
            stepSize = 1f
            value = potDiameterCm.toFloat()
            addOnChangeListener { _, v, _ ->
                potDiameterCm = v.roundToInt()
                binding.potValue.text = getString(R.string.water_calc_pot_value, potDiameterCm)
                recompute()
            }
        }
        binding.potValue.text = getString(R.string.water_calc_pot_value, potDiameterCm)
    }

    private fun setupTypeChips() {
        val labels = intArrayOf(
            R.string.light_species_general,
            R.string.light_species_succulent,
            R.string.light_species_tropical,
            R.string.light_species_fern,
            R.string.light_species_vegetable,
        )
        labels.forEachIndexed { idx, labelRes ->
            binding.typeChips.addView(
                buildChip(getString(labelRes), idx == typeIndex).apply {
                    setOnClickListener {
                        typeIndex = idx
                        recompute()
                    }
                }
            )
        }
    }

    private fun setupLightChips() {
        val labels = intArrayOf(
            R.string.water_calc_light_low,
            R.string.water_calc_light_medium,
            R.string.water_calc_light_bright,
        )
        labels.forEachIndexed { idx, labelRes ->
            binding.lightChips.addView(
                buildChip(getString(labelRes), idx == lightLevel).apply {
                    setOnClickListener {
                        lightLevel = idx
                        recompute()
                    }
                }
            )
        }
    }

    private fun setupSeasonChips() {
        val growing = buildChip(getString(R.string.water_calc_season_growing), !dormant)
        val dorm = buildChip(getString(R.string.water_calc_season_dormant), dormant)
        growing.setOnClickListener {
            dormant = false
            recompute()
        }
        dorm.setOnClickListener {
            dormant = true
            recompute()
        }
        binding.seasonChips.addView(growing)
        binding.seasonChips.addView(dorm)
    }

    private fun buildChip(label: String, checked: Boolean): Chip =
        Chip(this).apply {
            text = label
            isCheckable = true
            isChecked = checked
            isClickable = true
            setChipBackgroundColorResource(R.color.chip_bg_color)
            setEnsureMinTouchTargetSize(false)
        }

    /** A single-selection ChipGroup keeps exactly one chip checked; sync the
     *  visual state after any selection so the others clear. */
    private fun recompute() {
        syncChecked(binding.typeChips, typeIndex)
        syncChecked(binding.lightChips, lightLevel)
        // Season group: index 0 = growing, 1 = dormant.
        syncChecked(binding.seasonChips, if (dormant) 1 else 0)

        val (ml, days) = computeWatering(potDiameterCm, typeIndex, lightLevel, dormant)
        binding.waterAmount.text = ml.toString()
        binding.waterEvery.text = resources.getQuantityString(
            R.plurals.water_calc_every_days,
            days,
            days,
        )
        binding.waterTip.text = getString(tipForType(typeIndex))
    }

    private fun syncChecked(group: android.view.ViewGroup, checkedIndex: Int) {
        for (i in 0 until group.childCount) {
            (group.getChildAt(i) as? Chip)?.isChecked = (i == checkedIndex)
        }
    }

    private fun tipForType(type: Int): Int = when (type) {
        TYPE_SUCCULENT -> R.string.water_calc_tip_succulent
        TYPE_FERN -> R.string.water_calc_tip_fern
        else -> R.string.water_calc_tip_general
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
