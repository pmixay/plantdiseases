package com.plantdiseases.app.ui.camera

import kotlin.math.PI
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * The Water Calculator's estimate: how much water (ml) and how often (days)
 * a plant needs, from its pot, [PlantTypes] type, light and season. A
 * transparent heuristic with no sensor and no network.
 */
object WateringHeuristic {
    const val LIGHT_LOW = 0
    const val LIGHT_MEDIUM = 1
    const val LIGHT_BRIGHT = 2

    const val MIN_POT_CM = 6
    const val MAX_POT_CM = 40
    const val DEFAULT_POT_CM = 15

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
     * Returns (millilitres per watering, interval in days).
     *
     * @param potDiameterCm pot diameter in centimetres
     * @param typeIndex one of [PlantTypes] (0..4)
     * @param lightLevel one of LIGHT_* (0 low, 1 medium, 2 bright)
     * @param dormant true in the autumn/winter rest period
     */
    fun compute(
        potDiameterCm: Int,
        typeIndex: Int,
        lightLevel: Int,
        dormant: Boolean,
    ): Pair<Int, Int> {
        val d = potDiameterCm.coerceIn(MIN_POT_CM, MAX_POT_CM).toDouble()
        val type = typeIndex.coerceIn(PlantTypes.GENERAL, PlantTypes.VEGETABLE)
        val light = lightLevel.coerceIn(LIGHT_LOW, LIGHT_BRIGHT)

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
}
