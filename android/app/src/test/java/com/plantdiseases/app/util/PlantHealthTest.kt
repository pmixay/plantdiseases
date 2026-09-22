package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class PlantHealthTest {

    private fun scan(atMs: Long, healthy: Boolean, confidence: Float, notAPlant: Boolean = false) =
        PlantHealth.ScanInput(atMs, healthy, confidence, notAPlant)

    private val noAdherence = PlantHealth.Adherence(due = 0, done = 0)

    @Test
    fun evaluate_oneScan_isUnknownWithNullScoreAndOnePointSeries() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(scan(now, healthy = true, confidence = 0.9f)),
            hasActiveTreatmentPlan = false,
            adherence = noAdherence,
            nowMs = now,
        )
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.UNKNOWN)
        assertThat(verdict.score).isNull()
        assertThat(verdict.series).hasSize(1)
        assertThat(verdict.scanCount).isEqualTo(1)
    }

    @Test
    fun evaluate_twoHealthyHighConfidenceScans_isHealthyInTheNineties() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(
                scan(now - PlantHealth.WEEK_MS, healthy = true, confidence = 0.9f),
                scan(now, healthy = true, confidence = 0.95f),
            ),
            hasActiveTreatmentPlan = false,
            adherence = noAdherence,
            nowMs = now,
        )
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.HEALTHY)
        assertThat(verdict.score).isAtLeast(90)
    }

    @Test
    fun evaluate_healthyThenDiseasedAWeekLater_showsLargeNegativeDeltaAndNotHealthy() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(
                scan(now - 8L * PlantHealth.WEEK_MS / 7, healthy = true, confidence = 0.9f),
                scan(now, healthy = false, confidence = 0.8f),
            ),
            hasActiveTreatmentPlan = false,
            adherence = noAdherence,
            nowMs = now,
        )
        assertThat(verdict.weekDelta).isNotNull()
        assertThat(verdict.weekDelta!!).isLessThan(-40)
        assertThat(verdict.status).isNotEqualTo(PlantHealth.Status.HEALTHY)
    }

    @Test
    fun evaluate_activePlanWithGoodAdherence_isRecovering() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(
                scan(now - PlantHealth.WEEK_MS, healthy = false, confidence = 0.7f),
                scan(now, healthy = false, confidence = 0.6f),
            ),
            hasActiveTreatmentPlan = true,
            adherence = PlantHealth.Adherence(due = 5, done = 5),
            nowMs = now,
        )
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.RECOVERING)
    }

    @Test
    fun evaluate_newActivePlanWithNoDueWork_staysInTreatment() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(
                scan(now - PlantHealth.WEEK_MS, healthy = false, confidence = 0.7f),
                scan(now, healthy = false, confidence = 0.6f),
            ),
            hasActiveTreatmentPlan = true,
            adherence = PlantHealth.Adherence(due = 0, done = 0),
            nowMs = now,
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
        assertThat(PlantHealth.Adherence(due = 0, done = 0).ratio).isEqualTo(0f)
    }

    @Test
    fun evaluate_activePlanWithOnlyOneScan_stillShowsTreatment() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(scan(now, healthy = false, confidence = 0.9f)),
            hasActiveTreatmentPlan = true,
            adherence = noAdherence,
            nowMs = now,
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
        assertThat(verdict.score).isNull()
    }

    @Test
    fun evaluate_goodChecklistAdherenceWithoutScanImprovement_isNotRecovering() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(
                scan(now - PlantHealth.WEEK_MS, healthy = false, confidence = 0.5f),
                scan(now, healthy = false, confidence = 0.9f),
            ),
            hasActiveTreatmentPlan = true,
            adherence = PlantHealth.Adherence(due = 3, done = 3),
            nowMs = now,
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
    }

    @Test
    fun evaluate_activePlanWithPoorAdherence_isTreatment() {
        val now = 1_000_000_000L
        val verdict = PlantHealth.evaluate(
            scans = listOf(
                scan(now - PlantHealth.WEEK_MS, healthy = false, confidence = 0.7f),
                scan(now, healthy = false, confidence = 0.6f),
            ),
            hasActiveTreatmentPlan = true,
            adherence = PlantHealth.Adherence(due = 5, done = 1),
            nowMs = now,
        )
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
    }

    @Test
    fun evaluate_notAPlantScanBetweenTwoRealScans_isExcludedFromScoreAndSeries() {
        val now = 1_000_000_000L
        val withNap = PlantHealth.evaluate(
            scans = listOf(
                scan(now - 2 * PlantHealth.WEEK_MS, healthy = true, confidence = 0.9f),
                scan(now - PlantHealth.WEEK_MS, healthy = true, confidence = 0.0f, notAPlant = true),
                scan(now, healthy = true, confidence = 0.9f),
            ),
            hasActiveTreatmentPlan = false,
            adherence = noAdherence,
            nowMs = now,
        )
        val withoutNap = PlantHealth.evaluate(
            scans = listOf(
                scan(now - 2 * PlantHealth.WEEK_MS, healthy = true, confidence = 0.9f),
                scan(now, healthy = true, confidence = 0.9f),
            ),
            hasActiveTreatmentPlan = false,
            adherence = noAdherence,
            nowMs = now,
        )
        assertThat(withNap.score).isEqualTo(withoutNap.score)
        assertThat(withNap.series).hasSize(withoutNap.series.size)
    }

    @Test
    fun evaluate_allZeroInputs_isUnknownNotACrash() {
        val verdict = PlantHealth.evaluate(
            scans = emptyList(),
            hasActiveTreatmentPlan = false,
            adherence = PlantHealth.Adherence(due = 0, done = 0),
            nowMs = 0L,
        )
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.UNKNOWN)
        assertThat(verdict.scanCount).isEqualTo(0)
    }
}
