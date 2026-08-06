package com.plantdiseases.app.util

import com.plantdiseases.app.R

/**
 * Local growing advice per plant category, so the Tips section says something useful
 * even when the care plan carried no free text. Server guidance (key_action, placement,
 * acclimation, watering_method, red_flags) is appended, never replaced.
 */
object PlantTips {

    fun forCategory(category: PlantCategory): Int = when (category) {
        PlantCategory.FRUITING -> R.array.tips_fruiting
        PlantCategory.ROOT -> R.array.tips_root
        PlantCategory.LEAFY -> R.array.tips_leafy
        PlantCategory.FLOWERING -> R.array.tips_flowering
        PlantCategory.SUCCULENT -> R.array.tips_succulent
        PlantCategory.FOLIAGE -> R.array.tips_foliage
        PlantCategory.HERB -> R.array.tips_herb
    }
}
