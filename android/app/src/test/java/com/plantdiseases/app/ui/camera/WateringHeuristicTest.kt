package com.plantdiseases.app.ui.camera

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Verifies the watering heuristic ([WateringHeuristic.compute]).
 *
 * Type indices: 0 general, 1 succulent, 2 tropical, 3 fern, 4 vegetable.
 * Light:        0 low, 1 medium, 2 bright.
 */
class WateringHeuristicTest {

    @Test
    fun succulentNeedsLessWaterAndLongerIntervalThanFern() {
        val (succMl, succDays) = WateringHeuristic.compute(15, 1, 1, false)
        val (fernMl, fernDays) = WateringHeuristic.compute(15, 3, 1, false)
        assertThat(succMl).isLessThan(fernMl)
        assertThat(succDays).isGreaterThan(fernDays)
    }

    @Test
    fun biggerPotMeansMoreWaterAndLongerInterval() {
        val (smallMl, smallDays) = WateringHeuristic.compute(8, 2, 1, false)
        val (bigMl, bigDays) = WateringHeuristic.compute(30, 2, 1, false)
        assertThat(bigMl).isGreaterThan(smallMl)
        assertThat(bigDays).isAtLeast(smallDays)
    }

    @Test
    fun brightLightShortensIntervalVersusLowLight() {
        val (_, lowDays) = WateringHeuristic.compute(15, 2, 0, false)
        val (_, brightDays) = WateringHeuristic.compute(15, 2, 2, false)
        assertThat(brightDays).isLessThan(lowDays)
    }

    @Test
    fun dormantSeasonLengthensInterval() {
        val (_, growingDays) = WateringHeuristic.compute(15, 2, 1, false)
        val (_, dormantDays) = WateringHeuristic.compute(15, 2, 1, true)
        assertThat(dormantDays).isGreaterThan(growingDays)
    }

    @Test
    fun resultsStayWithinSaneBounds() {
        // Sweep the whole valid input space; ml and days must never escape the
        // clamped ranges the UI relies on.
        for (d in 6..40) {
            for (type in 0..4) {
                for (light in 0..2) {
                    for (dormant in listOf(false, true)) {
                        val (ml, days) = WateringHeuristic.compute(d, type, light, dormant)
                        assertThat(ml).isAtLeast(30)
                        assertThat(ml).isAtMost(2000)
                        assertThat(days).isAtLeast(1)
                        assertThat(days).isAtMost(30)
                        assertThat(ml % 10).isEqualTo(0)
                    }
                }
            }
        }
    }

    @Test
    fun outOfRangeInputsAreClampedNotCrashing() {
        val (ml, days) = WateringHeuristic.compute(999, 99, 99, false)
        assertThat(ml).isAtMost(2000)
        assertThat(days).isAtLeast(1)
    }
}
