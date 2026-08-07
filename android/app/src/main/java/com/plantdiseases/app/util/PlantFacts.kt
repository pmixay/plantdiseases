package com.plantdiseases.app.util

import com.plantdiseases.app.data.model.CarePlanResponse

/** 1 = easy, 2 = moderate, 3 = demanding. */
object PlantFacts {

    fun difficulty(plan: CarePlanResponse?, category: PlantCategory): Int {
        if (plan == null) {
            return if (category == PlantCategory.SUCCULENT) 1 else 2
        }
        var score = 0
        // Thirsty plants are unforgiving of a missed week.
        val days = plan.wateringDaysInt
        if (days != null && days <= 3) {
            score += 2
        } else if (days != null && days <= 5) {
            score += 1
        }
        // A humidity floor above 55% means the plant needs help indoors.
        val humidityMin = plan.humidityBand?.min ?: 0
        if (humidityMin >= 55) {
            score += 2
        } else if (humidityMin >= 45) {
            score += 1
        }
        // Narrow daytime temperature tolerance.
        val day = plan.temperatureC?.day
        if (day != null && day.size >= 2 && (day[1] - day[0]) in 1..8) score += 1
        // Succulents and foliage houseplants forgive almost everything.
        if (category == PlantCategory.SUCCULENT) score -= 2
        if (category == PlantCategory.FOLIAGE) score -= 1
        return when {
            score <= 0 -> 1
            score <= 2 -> 2
            else -> 3
        }
    }
}
