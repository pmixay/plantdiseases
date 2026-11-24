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
import kotlin.math.roundToInt

/**
 * Water Calculator — an advisory tool that estimates how much water (ml) and
 * how often (days) a plant needs, from four inputs the user can adjust: pot
 * diameter, plant type, the light at its spot, and the season. The plant type
 * is auto-detected from the launching plant's species (same genus→bucket
 * mapping the Light Meter uses) but stays user-overridable.
 *
 * Everything is a transparent heuristic ([WateringHeuristic]); there is no
 * sensor and no network. It mirrors the Light Meter's standalone-tool shape:
 * a gradient toolbar over a scrollable column of cards.
 */
class WaterCalculatorActivity : AppCompatActivity() {

    private lateinit var binding: ActivityWaterCalculatorBinding

    private var potDiameterCm = WateringHeuristic.DEFAULT_POT_CM
    private var typeIndex = PlantTypes.GENERAL
    private var lightLevel = WateringHeuristic.LIGHT_MEDIUM
    private var dormant = false

    companion object {
        /** Pass the plant's scientific (Latin) name so the plant-type chip
         *  pre-selects the right bucket. */
        const val EXTRA_SCIENTIFIC_NAME = "extra_scientific_name"

        /** Optional common / free-text name — auto-detection fallback. */
        const val EXTRA_COMMON_NAME = "extra_common_name"

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
        var pick = PlantTypes.fromName(sci)
        if (pick == PlantTypes.GENERAL) {
            val common = intent.getStringExtra(EXTRA_COMMON_NAME).orEmpty()
            if (common.isNotBlank()) pick = PlantTypes.fromName(common)
        }
        typeIndex = pick.coerceIn(PlantTypes.GENERAL, PlantTypes.VEGETABLE)
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
            valueFrom = WateringHeuristic.MIN_POT_CM.toFloat()
            valueTo = WateringHeuristic.MAX_POT_CM.toFloat()
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

        val (ml, days) = WateringHeuristic.compute(potDiameterCm, typeIndex, lightLevel, dormant)
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
        PlantTypes.SUCCULENT -> R.string.water_calc_tip_succulent
        PlantTypes.FERN -> R.string.water_calc_tip_fern
        else -> R.string.water_calc_tip_general
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
