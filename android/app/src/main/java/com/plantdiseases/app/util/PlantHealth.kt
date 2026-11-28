package com.plantdiseases.app.util

import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/**
 * The one health verdict — status, 0..100 score, week-over-week change and
 * chart series — that the plant-detail header, the plant-detail health card
 * and the room plant list all render, so those surfaces can't disagree.
 *
 * Design rule: a wrong verdict is worse than no verdict.
 * - Only recent evidence counts. Scans older than [SERIES_WINDOW_MS] say
 *   nothing about the plant today; with fewer than [MIN_SCANS_FOR_SCORE]
 *   recent scans there is no score and no guess (UNKNOWN, or TREATMENT
 *   while a plan is active).
 * - An unsure reading scores near the midpoint ([scoreOf]), so on its own
 *   it can make a plant read neither HEALTHY nor POOR.
 * - The alarming POOR status needs [POOR_CONFIRM_SCANS] consecutive
 *   confident disease readings spread over at least [POOR_CONFIRM_SPAN_MS]:
 *   one bad scan reads WATCH until another confirms it, and retakes in one
 *   sitting (same leaf, same light) confirm nothing.
 * - A change smaller than [DECLINE_DELTA_PP] / [IMPROVE_DELTA_PP] is
 *   classifier noise, not a trend.
 *
 * Every threshold lives in the tunables block below and nowhere else.
 * Pure Kotlin — no Room entities, no Android imports.
 */
object PlantHealth {

    /**
     * First match wins:
     * 1. Active treatment plan: RECOVERING when adherence is at least
     *    [ADHERENCE_GOOD] and the latest reading is healthy (score at least
     *    [SCORE_HEALTHY_MIN]) or the [Trend] is IMPROVING; else TREATMENT
     *    (also while there is no score yet).
     * 2. Fewer than [MIN_SCANS_FOR_SCORE] recent scans: UNKNOWN.
     * 3. The latest run of readings below [SCORE_WATCH_MIN] holds at least
     *    [POOR_CONFIRM_SCANS] readings and spans at least
     *    [POOR_CONFIRM_SPAN_MS]: POOR.
     * 4. Latest reading at least [SCORE_HEALTHY_MIN] and the [Trend] is not
     *    DECLINING: HEALTHY.
     * 5. Anything else: WATCH.
     */
    enum class Status { HEALTHY, WATCH, POOR, TREATMENT, RECOVERING, UNKNOWN }

    /** Direction of [Verdict.weekDelta]. A change strictly between
     *  [DECLINE_DELTA_PP] and [IMPROVE_DELTA_PP] is noise and reads STABLE. */
    enum class Trend { IMPROVING, STABLE, DECLINING }

    /** One scan as a dated 0..100 score. */
    data class Point(val atMs: Long, val score: Int)

    data class Verdict(
        val status: Status,
        /** The latest recent scan's 0..100 score. Null exactly when [series]
         *  holds fewer than [MIN_SCANS_FOR_SCORE] points. */
        val score: Int?,
        /** Score change, in points, from the newest recent scan at least
         *  [WEEK_MS] older than the latest one to the latest one. Measured
         *  between the two scans, not against the clock. Null when no such
         *  scan exists, and whenever [score] is null. */
        val weekDelta: Int?,
        /** Whole days between the two scans [weekDelta] compares (at least
         *  7). Null exactly when [weekDelta] is null. */
        val weekDeltaDays: Int?,
        /** The recent scans (inside [SERIES_WINDOW_MS]) as dated points,
         *  oldest first — exactly the evidence [score] and [status] come
         *  from. Never padded or interpolated; at least [MIN_SCANS_FOR_SCORE]
         *  points whenever [score] is non-null. */
        val series: List<Point>,
        /** Every plant scan seen, older ones included (not-a-plant scans
         *  excluded). 0 means the plant has never been scanned. */
        val scanCount: Int,
    ) {
        val trend: Trend? get() = weekDelta?.let { PlantHealth.trendOf(it) }
    }

    data class ScanInput(val atMs: Long, val isHealthy: Boolean, val confidence: Float, val isNotAPlant: Boolean)

    data class Adherence(val due: Int, val done: Int) {
        // No due work is absence of evidence, not perfect adherence: 0/0
        // reading 100% made a newly created treatment plan read "Recovering"
        // before a single checklist item was done. Clamped to 0..1 so
        // inconsistent counts can't push it negative or past 100%.
        val ratio: Float get() = if (due <= 0) 0f else (done.toFloat() / due).coerceIn(0f, 1f)
    }

    // ── tunables — the single place these live ─────────────────────────

    /** Recent scans needed before there is a score at all. */
    const val MIN_SCANS_FOR_SCORE = 2

    /** Minimum gap between the latest scan and the scan [Verdict.weekDelta]
     *  compares it with. */
    const val WEEK_MS = 7L * 24 * 60 * 60 * 1000

    /** Evidence window: only scans at most this old count toward the score,
     *  the status and the chart. */
    const val SERIES_WINDOW_MS = 60L * 24 * 60 * 60 * 1000

    /** At or above: a healthy reading. A healthy verdict scores
     *  50 + 50 × confidence, so this means healthy at confidence >= 0.5. */
    const val SCORE_HEALTHY_MIN = 75

    /** Below: a poor reading. A disease verdict scores 50 − 50 × confidence,
     *  so this means a disease at confidence > 0.5 — the mirror of
     *  [SCORE_HEALTHY_MIN], and the 0.5 the result screen falls back to for
     *  a low-confidence diagnosis. An edge just under 50 would catch every
     *  disease verdict however unsure: top-1 confidence is never below
     *  1 / (class count), which already puts the score several points
     *  under 50. */
    const val SCORE_WATCH_MIN = 25

    /** Consecutive poor readings, the latest included, needed for POOR. */
    const val POOR_CONFIRM_SCANS = 2

    /** Minimum time from the first to the latest of those consecutive poor
     *  readings. Scans minutes apart share the leaf, the light and the
     *  framing, so they repeat one reading rather than confirm it. */
    const val POOR_CONFIRM_SPAN_MS = 24L * 60 * 60 * 1000

    /** A [Verdict.weekDelta] at or below this is a real drop (Trend.DECLINING). */
    const val DECLINE_DELTA_PP = -10

    /** A [Verdict.weekDelta] at or above this is a real gain (Trend.IMPROVING). */
    const val IMPROVE_DELTA_PP = 10

    /** Share of due treatment / care work done for an active plan to count
     *  as kept up (one of the two RECOVERING conditions). */
    const val ADHERENCE_GOOD = 0.7f

    fun evaluate(
        scans: List<ScanInput>,
        hasActiveTreatmentPlan: Boolean,
        adherence: Adherence,
        nowMs: Long,
    ): Verdict {
        // Not-a-plant scans say nothing about the plant's condition. Ties on
        // the timestamp break on the score, so the verdict never depends on
        // the order the caller passed (the gentler reading counts as later).
        val readings = scans
            .filterNot { it.isNotAPlant }
            .map { Point(it.atMs, scoreOf(it)) }
            .sortedWith(compareBy<Point> { it.atMs }.thenBy { it.score })
        val series = readings.filter { nowMs - it.atMs <= SERIES_WINDOW_MS }

        if (series.size < MIN_SCANS_FOR_SCORE) {
            return Verdict(
                status = if (hasActiveTreatmentPlan) Status.TREATMENT else Status.UNKNOWN,
                score = null,
                weekDelta = null,
                weekDeltaDays = null,
                series = series,
                scanCount = readings.size,
            )
        }

        val latest = series.last()
        val baseline = series.lastOrNull { latest.atMs - it.atMs >= WEEK_MS }
        val weekDelta = baseline?.let { latest.score - it.score }
        val weekDeltaDays = baseline?.let { TimeUnit.MILLISECONDS.toDays(latest.atMs - it.atMs).toInt() }
        val trend = weekDelta?.let { trendOf(it) }

        val readsHealthy = latest.score >= SCORE_HEALTHY_MIN
        val treatmentWorking = adherence.ratio >= ADHERENCE_GOOD &&
            (readsHealthy || trend == Trend.IMPROVING)
        val poorRun = series.takeLastWhile { it.score < SCORE_WATCH_MIN }
        val confirmedPoor = poorRun.size >= POOR_CONFIRM_SCANS &&
            latest.atMs - poorRun.first().atMs >= POOR_CONFIRM_SPAN_MS

        val status = when {
            hasActiveTreatmentPlan && treatmentWorking -> Status.RECOVERING
            hasActiveTreatmentPlan -> Status.TREATMENT
            confirmedPoor -> Status.POOR
            readsHealthy && trend != Trend.DECLINING -> Status.HEALTHY
            else -> Status.WATCH
        }

        return Verdict(
            status = status,
            score = latest.score,
            weekDelta = weekDelta,
            weekDeltaDays = weekDeltaDays,
            series = series,
            scanCount = readings.size,
        )
    }

    /** The [Trend] a score change of [delta] points represents. */
    fun trendOf(delta: Int): Trend = when {
        delta <= DECLINE_DELTA_PP -> Trend.DECLINING
        delta >= IMPROVE_DELTA_PP -> Trend.IMPROVING
        else -> Trend.STABLE
    }

    /** Healthy verdict -> 50 + 50 × confidence (50..100); disease verdict ->
     *  50 − 50 × confidence (0..50), so an unsure reading lands near 50
     *  either way. Rounded rather than truncated so float noise can't shave
     *  a point off (0.6f × 50 is 30.000002; truncation scores that 19, not
     *  20). A NaN confidence counts as 0 — no information — instead of
     *  crashing the rounding. Not-a-plant scans never get here; [evaluate]
     *  drops them first. */
    private fun scoreOf(scan: ScanInput): Int {
        val confidence = if (scan.confidence.isNaN()) 0f else scan.confidence.coerceIn(0f, 1f)
        val raw = if (scan.isHealthy) 50 + confidence * 50 else 50 - confidence * 50
        return raw.roundToInt()
    }
}
