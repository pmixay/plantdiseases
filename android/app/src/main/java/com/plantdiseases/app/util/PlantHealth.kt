package com.plantdiseases.app.util

/**
 * Derives the single healthy / recovering / declining verdict the Garden
 * card accent and the plant-profile header both read.
 *
 * ALL thresholds live in the companion block below and nowhere else —
 * ROADMAP §Garden refactor: "Thresholds must live in one tunable place."
 * This is the ROADMAP's health-heuristic human gate: a wrong verdict is
 * worse than no verdict, so a plant with too little history reads UNKNOWN
 * rather than guessing. Pure Kotlin — no Room entities, no Android imports.
 */
object PlantHealth {

    enum class Status { HEALTHY, WATCH, TREATMENT, RECOVERING, UNKNOWN }

    data class Point(val atMs: Long, val score: Int)

    data class Verdict(
        val status: Status,
        /** 0..100, or null when there isn't enough data to be honest. */
        val score: Int?,
        /** Percentage-point change vs the closest scan >= WEEK_MS old.
         *  Null when no such earlier scan exists. */
        val weekDelta: Int?,
        /** Dated points for the chart, oldest first. Never padded or
         *  interpolated — may be shorter than the requested window. */
        val series: List<Point>,
        val scanCount: Int,
    )

    data class ScanInput(val atMs: Long, val isHealthy: Boolean, val confidence: Float, val isNotAPlant: Boolean)

    data class Adherence(val due: Int, val done: Int) {
        // No due work is absence of evidence, not perfect adherence. Treating
        // 0/0 as 100% made a newly-created treatment plan instantly read
        // "Recovering" before the user completed a single checklist item.
        val ratio: Float get() = if (due == 0) 0f else done.toFloat() / due
    }

    // ── tunables — the single place these live ─────────────────────────
    const val MIN_SCANS_FOR_SCORE = 2
    const val WEEK_MS = 7L * 24 * 60 * 60 * 1000
    const val SERIES_WINDOW_MS = 60L * 24 * 60 * 60 * 1000
    const val SCORE_HEALTHY_MIN = 75
    const val SCORE_WATCH_MIN = 45
    const val DECLINE_DELTA_PP = -10
    const val ADHERENCE_GOOD = 0.7f

    fun evaluate(
        scans: List<ScanInput>,
        hasActiveTreatmentPlan: Boolean,
        adherence: Adherence,
        nowMs: Long,
    ): Verdict {
        val included = scans.filterNot { it.isNotAPlant }.sortedBy { it.atMs }
        val series = included
            .filter { nowMs - it.atMs <= SERIES_WINDOW_MS }
            .map { Point(it.atMs, scoreOf(it)) }

        if (included.size < MIN_SCANS_FOR_SCORE) {
            return Verdict(
                status = if (hasActiveTreatmentPlan) Status.TREATMENT else Status.UNKNOWN,
                score = null,
                weekDelta = null,
                series = series,
                scanCount = included.size,
            )
        }

        val latest = included.last()
        val score = scoreOf(latest)
        val weekAgoOrOlder = included.dropLast(1).lastOrNull { nowMs - it.atMs >= WEEK_MS }
        val weekDelta = weekAgoOrOlder?.let { score - scoreOf(it) }

        val status = when {
            hasActiveTreatmentPlan &&
                adherence.due > 0 &&
                adherence.ratio >= ADHERENCE_GOOD &&
                (latest.isHealthy || (weekDelta != null && weekDelta > 0)) -> Status.RECOVERING
            hasActiveTreatmentPlan -> Status.TREATMENT
            score >= SCORE_HEALTHY_MIN && (weekDelta == null || weekDelta > DECLINE_DELTA_PP) -> Status.HEALTHY
            score >= SCORE_WATCH_MIN -> Status.WATCH
            else -> Status.WATCH // declining — same bucket, UI reads the score/delta for the accent
        }

        return Verdict(
            status = status,
            score = score,
            weekDelta = weekDelta,
            series = series,
            scanCount = included.size,
        )
    }

    /** Healthy verdict -> [50, 100] scaled by confidence; disease verdict
     *  -> [0, 50] scaled by (1 - confidence). A not-a-plant scan never
     *  reaches this function — it's excluded by the caller, since it says
     *  nothing about the plant's condition. */
    private fun scoreOf(scan: ScanInput): Int {
        val raw = if (scan.isHealthy) {
            50 + scan.confidence * 50
        } else {
            50 - scan.confidence * 50
        }
        return raw.toInt().coerceIn(0, 100)
    }
}
