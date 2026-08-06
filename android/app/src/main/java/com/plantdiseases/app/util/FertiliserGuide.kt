package com.plantdiseases.app.util

import com.plantdiseases.app.R

/**
 * Local feeding guidance per plant category. The server's care plan only carries an
 * interval and, occasionally, an NPK string, which left the Fertiliser section with a
 * single sentence for most species. These defaults fill the gap offline; any field the
 * server does supply wins over the default at render time.
 */
data class FeedingGuide(
    /** Suggested NPK ratio, e.g. "5-10-10". */
    val npk: String,
    /** Percent of the label dose that suits this group indoors. */
    val dosePercent: Int,
    /** Weeks between feeds during active growth. */
    val intervalWeeks: Int,
    /** Months (1-12) in which to feed at all. */
    val seasonMonths: IntRange,
    /** Soil / substrate note. */
    val soilRes: Int,
    /** Two to three category-specific cautions. */
    val cautionRes: List<Int>,
)

object FertiliserGuide {

    fun forCategory(category: PlantCategory): FeedingGuide = when (category) {
        PlantCategory.FRUITING -> FeedingGuide(
            npk = "5-10-10",
            dosePercent = 50,
            intervalWeeks = 2,
            seasonMonths = 3..9,
            soilRes = R.string.feed_soil_fruiting,
            cautionRes = listOf(
                R.string.feed_caution_fruiting_nitrogen,
                R.string.feed_caution_fruiting_calcium,
            ),
        )
        PlantCategory.ROOT -> FeedingGuide(
            npk = "3-8-10",
            dosePercent = 50,
            intervalWeeks = 3,
            seasonMonths = 3..8,
            soilRes = R.string.feed_soil_root,
            cautionRes = listOf(
                R.string.feed_caution_root_nitrogen,
                R.string.feed_caution_root_fresh_manure,
            ),
        )
        PlantCategory.LEAFY -> FeedingGuide(
            npk = "10-5-5",
            dosePercent = 50,
            intervalWeeks = 1,
            seasonMonths = 1..12,
            soilRes = R.string.feed_soil_leafy,
            cautionRes = listOf(
                R.string.feed_caution_leafy_nitrate,
                R.string.feed_caution_leafy_harvest,
            ),
        )
        PlantCategory.FLOWERING -> FeedingGuide(
            npk = "10-30-20",
            dosePercent = 50,
            intervalWeeks = 2,
            seasonMonths = 3..10,
            soilRes = R.string.feed_soil_flowering,
            cautionRes = listOf(
                R.string.feed_caution_flowering_switch,
                R.string.feed_caution_flowering_rest,
            ),
        )
        PlantCategory.SUCCULENT -> FeedingGuide(
            npk = "2-7-7",
            dosePercent = 25,
            intervalWeeks = 6,
            seasonMonths = 4..8,
            soilRes = R.string.feed_soil_succulent,
            cautionRes = listOf(
                R.string.feed_caution_succulent_winter,
                R.string.feed_caution_succulent_etiolation,
            ),
        )
        PlantCategory.FOLIAGE -> FeedingGuide(
            npk = "3-1-2",
            dosePercent = 50,
            intervalWeeks = 4,
            seasonMonths = 3..10,
            soilRes = R.string.feed_soil_foliage,
            cautionRes = listOf(
                R.string.feed_caution_foliage_salt,
                R.string.feed_caution_foliage_lowlight,
            ),
        )
        PlantCategory.HERB -> FeedingGuide(
            npk = "10-5-5",
            dosePercent = 50,
            intervalWeeks = 3,
            seasonMonths = 3..10,
            soilRes = R.string.feed_soil_herb,
            cautionRes = listOf(
                R.string.feed_caution_herb_aroma,
                R.string.feed_caution_herb_edible,
            ),
        )
    }

    /** Rules that hold for every houseplant, shown under every category. */
    val universalRules = listOf(
        R.string.feed_rule_never_dry,
        R.string.feed_rule_never_sick,
        R.string.feed_rule_after_repot,
        R.string.feed_rule_flush,
        R.string.feed_rule_cold,
    )
}
