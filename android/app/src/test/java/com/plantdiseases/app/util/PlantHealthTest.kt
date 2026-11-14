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

    // ── score scale ─────────────────────────────────────────────────────

    @Test
    fun score_isFiftyPlusOrMinusFiftyTimesConfidence() {
        assertThat(steadyScore(healthy = true, confidence = 1f)).isEqualTo(100)
        assertThat(steadyScore(healthy = true, confidence = 0.5f)).isEqualTo(75)
        assertThat(steadyScore(healthy = true, confidence = 0f)).isEqualTo(50)
        assertThat(steadyScore(healthy = false, confidence = 0f)).isEqualTo(50)
        assertThat(steadyScore(healthy = false, confidence = 0.5f)).isEqualTo(25)
        assertThat(steadyScore(healthy = false, confidence = 1f)).isEqualTo(0)
    }

    @Test
    fun score_roundsInsteadOfTruncatingFloatNoise() {
        // 0.6f * 50 is 30.000002 in float; truncation scored this 19.
        assertThat(steadyScore(healthy = false, confidence = 0.6f)).isEqualTo(20)
        assertThat(steadyScore(healthy = true, confidence = 0.7f)).isEqualTo(85)
    }

    @Test
    fun score_nanOrOutOfRangeConfidenceIsClampedNotACrash() {
        assertThat(steadyScore(healthy = true, confidence = Float.NaN)).isEqualTo(50)
        assertThat(steadyScore(healthy = false, confidence = Float.NaN)).isEqualTo(50)
        assertThat(steadyScore(healthy = true, confidence = 1.5f)).isEqualTo(100)
        assertThat(steadyScore(healthy = false, confidence = -0.5f)).isEqualTo(50)
    }

    // ── status bands ────────────────────────────────────────────────────

    @Test
    fun evaluate_latestExactlyAtHealthyMin_isHealthy_oneBelow_isWatch() {
        val atMin = evaluate(
            scan(NOW - DAY, healthy = true, confidence = 0.5f),
            scan(NOW, healthy = true, confidence = 0.5f),
        )
        val below = evaluate(
            scan(NOW - DAY, healthy = true, confidence = 0.48f),
            scan(NOW, healthy = true, confidence = 0.48f),
        )

        assertThat(atMin.score).isEqualTo(PlantHealth.SCORE_HEALTHY_MIN)
        assertThat(atMin.status).isEqualTo(PlantHealth.Status.HEALTHY)
        assertThat(below.score).isEqualTo(PlantHealth.SCORE_HEALTHY_MIN - 1)
        assertThat(below.status).isEqualTo(PlantHealth.Status.WATCH)
    }

    @Test
    fun evaluate_unsureHealthyReadings_areWatchNotHealthy() {
        val verdict = evaluate(
            scan(NOW - DAY, healthy = true, confidence = 0.3f),
            scan(NOW, healthy = true, confidence = 0.3f),
        )

        assertThat(verdict.score).isEqualTo(65)
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.WATCH)
    }

    @Test
    fun evaluate_twoConfidentDiseaseReadings_isPoor() {
        val verdict = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.8f),
            scan(NOW, healthy = false, confidence = 0.9f),
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.POOR)
        assertThat(verdict.score).isEqualTo(5)
    }

    @Test
    fun evaluate_poorNeedsEveryConfirmingReadingBelowWatchMin() {
        val bothBelow = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.52f),
            scan(NOW, healthy = false, confidence = 0.52f),
        )
        val earlierAtMin = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.5f),
            scan(NOW, healthy = false, confidence = 0.52f),
        )
        val latestAtMin = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.52f),
            scan(NOW, healthy = false, confidence = 0.5f),
        )

        assertThat(bothBelow.score).isEqualTo(PlantHealth.SCORE_WATCH_MIN - 1)
        assertThat(bothBelow.status).isEqualTo(PlantHealth.Status.POOR)
        assertThat(earlierAtMin.status).isEqualTo(PlantHealth.Status.WATCH)
        assertThat(latestAtMin.score).isEqualTo(PlantHealth.SCORE_WATCH_MIN)
        assertThat(latestAtMin.status).isEqualTo(PlantHealth.Status.WATCH)
    }

    @Test
    fun evaluate_lowConfidenceDiseaseReadings_neverReadPoor() {
        // Disease verdicts the result screen flags as low confidence: not
        // evidence enough for an alarming status, however many agree.
        val verdict = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.3f),
            scan(NOW, healthy = false, confidence = 0.4f),
        )

        assertThat(verdict.score).isEqualTo(30)
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.WATCH)
    }

    @Test
    fun evaluate_oneConfidentDiseaseReadingAfterHealthy_isWatchUntilASecondConfirmsIt() {
        val healthyBefore = scan(NOW - 8 * DAY, healthy = true, confidence = 0.9f)
        val unconfirmed = evaluate(
            healthyBefore,
            scan(NOW, healthy = false, confidence = 0.9f),
        )
        val confirmed = evaluate(
            healthyBefore,
            scan(NOW - DAY, healthy = false, confidence = 0.8f),
            scan(NOW, healthy = false, confidence = 0.9f),
        )

        assertThat(unconfirmed.status).isEqualTo(PlantHealth.Status.WATCH)
        assertThat(unconfirmed.weekDelta).isEqualTo(-90)
        assertThat(unconfirmed.trend).isEqualTo(PlantHealth.Trend.DECLINING)
        assertThat(confirmed.status).isEqualTo(PlantHealth.Status.POOR)
        assertThat(confirmed.weekDelta).isEqualTo(-90)
    }

    @Test
    fun evaluate_activeTreatmentPlan_takesPrecedenceOverPoor() {
        val verdict = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.8f),
            scan(NOW, healthy = false, confidence = 0.9f),
            plan = true,
            adherence = PlantHealth.Adherence(due = 5, done = 1),
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
        assertThat(verdict.score).isEqualTo(5)
    }

    @Test
    fun evaluate_dropOfNineStaysHealthy_dropOfTenIsWatch() {
        val baseline = scan(NOW - PlantHealth.WEEK_MS, healthy = true, confidence = 0.7f)
        val dropNine = evaluate(baseline, scan(NOW, healthy = true, confidence = 0.52f))
        val dropTen = evaluate(baseline, scan(NOW, healthy = true, confidence = 0.5f))

        assertThat(dropNine.weekDelta).isEqualTo(-9)
        assertThat(dropNine.trend).isEqualTo(PlantHealth.Trend.STABLE)
        assertThat(dropNine.status).isEqualTo(PlantHealth.Status.HEALTHY)
        assertThat(dropTen.weekDelta).isEqualTo(PlantHealth.DECLINE_DELTA_PP)
        assertThat(dropTen.trend).isEqualTo(PlantHealth.Trend.DECLINING)
        assertThat(dropTen.status).isEqualTo(PlantHealth.Status.WATCH)
    }

    // ── treatment plan: RECOVERING vs TREATMENT ─────────────────────────

    @Test
    fun evaluate_activePlan_gainOfTenRecovers_gainOfNineStaysTreatment() {
        val baseline = scan(NOW - PlantHealth.WEEK_MS, healthy = false, confidence = 0.9f)
        val goodAdherence = PlantHealth.Adherence(due = 5, done = 5)
        val gainTen = evaluate(baseline, scan(NOW, healthy = false, confidence = 0.7f), plan = true, adherence = goodAdherence)
        val gainNine = evaluate(baseline, scan(NOW, healthy = false, confidence = 0.72f), plan = true, adherence = goodAdherence)

        assertThat(gainTen.weekDelta).isEqualTo(PlantHealth.IMPROVE_DELTA_PP)
        assertThat(gainTen.trend).isEqualTo(PlantHealth.Trend.IMPROVING)
        assertThat(gainTen.status).isEqualTo(PlantHealth.Status.RECOVERING)
        assertThat(gainNine.weekDelta).isEqualTo(9)
        assertThat(gainNine.status).isEqualTo(PlantHealth.Status.TREATMENT)
    }

    @Test
    fun evaluate_activePlan_noiseLevelGain_staysTreatment() {
        // 15 -> 20 is within classifier noise, not a recovery.
        val verdict = evaluate(
            scan(NOW - PlantHealth.WEEK_MS, healthy = false, confidence = 0.7f),
            scan(NOW, healthy = false, confidence = 0.6f),
            plan = true,
            adherence = PlantHealth.Adherence(due = 5, done = 5),
        )

        assertThat(verdict.weekDelta).isEqualTo(5)
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
    }

    @Test
    fun evaluate_activePlan_healthyLatestRecoversOnlyWhenConfident() {
        val diseased = scan(NOW - DAY, healthy = false, confidence = 0.9f)
        val goodAdherence = PlantHealth.Adherence(due = 4, done = 4)
        val confident = evaluate(diseased, scan(NOW, healthy = true, confidence = 0.5f), plan = true, adherence = goodAdherence)
        val unsure = evaluate(diseased, scan(NOW, healthy = true, confidence = 0.3f), plan = true, adherence = goodAdherence)

        assertThat(confident.weekDelta).isNull()
        assertThat(confident.status).isEqualTo(PlantHealth.Status.RECOVERING)
        assertThat(unsure.status).isEqualTo(PlantHealth.Status.TREATMENT)
    }

    @Test
    fun evaluate_activePlan_adherenceExactlyGoodRecovers_justBelowDoesNot() {
        val scans = listOf(
            scan(NOW - DAY, healthy = false, confidence = 0.9f),
            scan(NOW, healthy = true, confidence = 0.9f),
        )
        val atThreshold = PlantHealth.evaluate(scans, true, PlantHealth.Adherence(due = 10, done = 7), NOW)
        val below = PlantHealth.evaluate(scans, true, PlantHealth.Adherence(due = 10, done = 6), NOW)

        assertThat(atThreshold.status).isEqualTo(PlantHealth.Status.RECOVERING)
        assertThat(below.status).isEqualTo(PlantHealth.Status.TREATMENT)
    }

    @Test
    fun adherenceRatio_isZeroWithoutDueWorkAndClampedToZeroOne() {
        assertThat(PlantHealth.Adherence(due = 0, done = 0).ratio).isEqualTo(0f)
        assertThat(PlantHealth.Adherence(due = 0, done = 3).ratio).isEqualTo(0f)
        assertThat(PlantHealth.Adherence(due = -2, done = -2).ratio).isEqualTo(0f)
        assertThat(PlantHealth.Adherence(due = 10, done = 7).ratio).isWithin(1e-6f).of(0.7f)
        assertThat(PlantHealth.Adherence(due = 10, done = 12).ratio).isEqualTo(1f)
        assertThat(PlantHealth.Adherence(due = 10, done = -3).ratio).isEqualTo(0f)
    }

    // ── evidence window ─────────────────────────────────────────────────

    @Test
    fun evaluate_onlyScansOlderThanTheWindow_hasNoScoreHowManyEver() {
        val verdict = evaluate(
            scan(NOW - 70 * DAY, healthy = true, confidence = 0.9f),
            scan(NOW - 65 * DAY, healthy = true, confidence = 0.9f),
            scan(NOW - 61 * DAY, healthy = true, confidence = 0.9f),
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.UNKNOWN)
        assertThat(verdict.score).isNull()
        assertThat(verdict.weekDelta).isNull()
        assertThat(verdict.weekDeltaDays).isNull()
        assertThat(verdict.trend).isNull()
        assertThat(verdict.series).isEmpty()
        assertThat(verdict.scanCount).isEqualTo(3)
    }

    @Test
    fun evaluate_staleScansWithActivePlan_isTreatmentWithoutScore() {
        val verdict = evaluate(
            scan(NOW - 90 * DAY, healthy = false, confidence = 0.9f),
            scan(NOW - 80 * DAY, healthy = false, confidence = 0.9f),
            plan = true,
            adherence = PlantHealth.Adherence(due = 3, done = 3),
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.TREATMENT)
        assertThat(verdict.score).isNull()
    }

    @Test
    fun evaluate_scanExactlyWindowOldCounts_oneMillisecondOlderDoesNot() {
        val atEdge = evaluate(
            scan(NOW - PlantHealth.SERIES_WINDOW_MS, healthy = true, confidence = 0.9f),
            scan(NOW, healthy = true, confidence = 0.9f),
        )
        val pastEdge = evaluate(
            scan(NOW - PlantHealth.SERIES_WINDOW_MS - 1, healthy = true, confidence = 0.9f),
            scan(NOW, healthy = true, confidence = 0.9f),
        )

        assertThat(atEdge.series).hasSize(2)
        assertThat(atEdge.score).isEqualTo(95)
        assertThat(pastEdge.series).hasSize(1)
        assertThat(pastEdge.score).isNull()
        assertThat(pastEdge.status).isEqualTo(PlantHealth.Status.UNKNOWN)
        assertThat(pastEdge.scanCount).isEqualTo(2)
    }

    @Test
    fun evaluate_scanOutsideTheWindow_isNeverTheWeekDeltaBaseline() {
        val verdict = evaluate(
            scan(NOW - 70 * DAY, healthy = false, confidence = 0.9f),
            scan(NOW - 2 * DAY, healthy = true, confidence = 0.9f),
            scan(NOW, healthy = true, confidence = 0.9f),
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.HEALTHY)
        assertThat(verdict.weekDelta).isNull()
        assertThat(verdict.series.map { it.atMs }).containsExactly(NOW - 2 * DAY, NOW).inOrder()
        assertThat(verdict.scanCount).isEqualTo(3)
    }

    @Test
    fun evaluate_wheneverThereIsAScore_seriesHoldsAtLeastTheMinimumScans() {
        val verdicts = listOf(
            evaluate(scan(NOW - DAY, healthy = true, confidence = 0.9f), scan(NOW, healthy = true, confidence = 0.9f)),
            evaluate(scan(NOW - 90 * DAY, healthy = true, confidence = 0.9f), scan(NOW, healthy = true, confidence = 0.9f)),
            evaluate(scan(NOW, healthy = true, confidence = 0.9f), scan(NOW - DAY, healthy = false, confidence = 0.9f, notAPlant = true)),
        )

        verdicts.forEach { verdict ->
            if (verdict.score != null) {
                assertThat(verdict.series.size).isAtLeast(PlantHealth.MIN_SCANS_FOR_SCORE)
            } else {
                assertThat(verdict.series.size).isLessThan(PlantHealth.MIN_SCANS_FOR_SCORE)
            }
        }
        assertThat(verdicts.map { it.score != null }).containsExactly(true, false, false).inOrder()
    }

    // ── weekDelta ───────────────────────────────────────────────────────

    @Test
    fun evaluate_weekDeltaIsMeasuredFromTheLatestScanNotFromNow() {
        // Both scans are over a week old but only a day apart: comparing
        // them is not a week-over-week change.
        val verdict = evaluate(
            scan(NOW - 10 * DAY, healthy = true, confidence = 0.9f),
            scan(NOW - 9 * DAY, healthy = false, confidence = 0.8f),
        )

        assertThat(verdict.weekDelta).isNull()
        assertThat(verdict.weekDeltaDays).isNull()
        assertThat(verdict.status).isEqualTo(PlantHealth.Status.WATCH)
    }

    @Test
    fun evaluate_weekDeltaComparesWithTheNewestScanAtLeastAWeekBeforeTheLatest() {
        val verdict = evaluate(
            scan(NOW - 15 * DAY, healthy = true, confidence = 0.9f),
            scan(NOW - 8 * DAY, healthy = true, confidence = 0.7f),
            scan(NOW - 3 * DAY, healthy = true, confidence = 0.5f),
            scan(NOW, healthy = true, confidence = 0.6f),
        )

        assertThat(verdict.score).isEqualTo(80)
        assertThat(verdict.weekDelta).isEqualTo(80 - 85)
        assertThat(verdict.weekDeltaDays).isEqualTo(8)
        assertThat(verdict.trend).isEqualTo(PlantHealth.Trend.STABLE)
    }

    @Test
    fun evaluate_baselineExactlyAWeekBeforeCounts_justUnderAWeekDoesNot() {
        val exactlyWeek = evaluate(
            scan(NOW - PlantHealth.WEEK_MS, healthy = true, confidence = 0.9f),
            scan(NOW, healthy = true, confidence = 0.9f),
        )
        val underWeek = evaluate(
            scan(NOW - PlantHealth.WEEK_MS + 1, healthy = true, confidence = 0.9f),
            scan(NOW, healthy = true, confidence = 0.9f),
        )

        assertThat(exactlyWeek.weekDelta).isEqualTo(0)
        assertThat(exactlyWeek.weekDeltaDays).isEqualTo(7)
        assertThat(underWeek.weekDelta).isNull()
        assertThat(underWeek.weekDeltaDays).isNull()
    }

    @Test
    fun trendOf_noiseBandIsStable_thresholdsAreInclusive() {
        assertThat(PlantHealth.trendOf(PlantHealth.DECLINE_DELTA_PP)).isEqualTo(PlantHealth.Trend.DECLINING)
        assertThat(PlantHealth.trendOf(PlantHealth.DECLINE_DELTA_PP + 1)).isEqualTo(PlantHealth.Trend.STABLE)
        assertThat(PlantHealth.trendOf(0)).isEqualTo(PlantHealth.Trend.STABLE)
        assertThat(PlantHealth.trendOf(PlantHealth.IMPROVE_DELTA_PP - 1)).isEqualTo(PlantHealth.Trend.STABLE)
        assertThat(PlantHealth.trendOf(PlantHealth.IMPROVE_DELTA_PP)).isEqualTo(PlantHealth.Trend.IMPROVING)
    }

    // ── ordering, ties, not-a-plant ─────────────────────────────────────

    @Test
    fun evaluate_isIndependentOfInputOrder_andSeriesIsOldestFirst() {
        // The repository hands scans over newest first.
        val newestFirst = listOf(
            scan(NOW, healthy = true, confidence = 0.9f),
            scan(NOW - 8 * DAY, healthy = true, confidence = 0.7f),
            scan(NOW - 20 * DAY, healthy = false, confidence = 0.9f),
        )
        val fromNewestFirst = PlantHealth.evaluate(newestFirst, false, noAdherence, NOW)
        val fromOldestFirst = PlantHealth.evaluate(newestFirst.reversed(), false, noAdherence, NOW)

        assertThat(fromNewestFirst).isEqualTo(fromOldestFirst)
        assertThat(fromNewestFirst.series.map { it.atMs }).isInOrder()
        assertThat(fromNewestFirst.score).isEqualTo(95)
        assertThat(fromNewestFirst.weekDelta).isEqualTo(95 - 85)
    }

    @Test
    fun evaluate_timestampTie_isDeterministicAndTakesTheGentlerReadingAsLatest() {
        val earlier = scan(NOW - DAY, healthy = false, confidence = 0.9f)
        val tiedDisease = scan(NOW, healthy = false, confidence = 0.9f)
        val tiedHealthy = scan(NOW, healthy = true, confidence = 0.9f)
        val one = evaluate(earlier, tiedDisease, tiedHealthy)
        val other = evaluate(earlier, tiedHealthy, tiedDisease)

        assertThat(one).isEqualTo(other)
        assertThat(one.score).isEqualTo(95)
        assertThat(one.status).isNotEqualTo(PlantHealth.Status.POOR)
    }

    @Test
    fun evaluate_notAPlantScansDoNotCountTowardTheMinimum() {
        val verdict = evaluate(
            scan(NOW - DAY, healthy = false, confidence = 0.9f, notAPlant = true),
            scan(NOW, healthy = true, confidence = 0.9f),
        )

        assertThat(verdict.status).isEqualTo(PlantHealth.Status.UNKNOWN)
        assertThat(verdict.score).isNull()
        assertThat(verdict.scanCount).isEqualTo(1)
    }

    private fun evaluate(
        vararg scans: PlantHealth.ScanInput,
        plan: Boolean = false,
        adherence: PlantHealth.Adherence = noAdherence,
    ): PlantHealth.Verdict = PlantHealth.evaluate(scans.toList(), plan, adherence, NOW)

    /** Score of two identical readings a day apart (no trend involved). */
    private fun steadyScore(healthy: Boolean, confidence: Float): Int? =
        evaluate(scan(NOW - DAY, healthy, confidence), scan(NOW, healthy, confidence)).score

    private companion object {
        const val NOW = 1_700_000_000_000L
        const val DAY = 24L * 60 * 60 * 1000
    }
}
