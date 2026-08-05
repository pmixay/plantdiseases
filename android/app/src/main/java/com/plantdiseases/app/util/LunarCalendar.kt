package com.plantdiseases.app.util

import java.time.LocalDate
import java.time.YearMonth
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.sin

/**
 * Offline lunar-gardening calculator.
 *
 * Everything here is arithmetic on the date (plus an optional [PlantCategory]
 * that tailors the advice) — no network, no permissions, no stored state — so
 * the lunar section renders instantly and works offline for every species in
 * the app, however that species arrived (the plant set is open-ended: free
 * text from PlantNet/Wikipedia/community scans, never a fixed catalog).
 *
 * Accuracy: the Moon's ecliptic longitude uses the standard low-precision
 * series (four largest terms), good to roughly ±0.3°, i.e. the zodiac sign is
 * right except within ~half an hour of a sign boundary. That is far inside the
 * tolerance of a gardening calendar.
 */
object LunarCalendar {

    /** Mean synodic month, days. */
    private const val SYNODIC_MONTH = 29.530588853

    /** Age epoch: JD 2451550.1 = 2000-01-06 14:24 UT, the conventional mean
     *  new-moon reference for this series. Ages are therefore mean, not true,
     *  conjunctions — the two differ by up to ~14 h, well inside the tolerance
     *  of a day-resolution gardening calendar. */
    private const val NEW_MOON_EPOCH_JD = 2451550.1

    /** Julian Day of J2000.0 (2000-01-01 12:00 TT). */
    private const val J2000_JD = 2451545.0

    /**
     * The calendar only navigates within this window; see [clampMonth]/[clampDate].
     *
     * Deliberately a wide FIXED window rather than a narrow one, and rather than
     * something derived from `LocalDate.now()`: a two-year window dead-ends while
     * the installed build is still in use (a user on an old version simply cannot
     * page forward), and a now()-derived window would make every result here
     * depend on the wall clock, which is untestable. The four-term series stays
     * comfortably inside a 30° zodiac bin across this span.
     */
    val MIN_MONTH: YearMonth = YearMonth.of(2020, 1)
    val MAX_MONTH: YearMonth = YearMonth.of(2040, 12)
    val MIN_DATE: LocalDate = MIN_MONTH.atDay(1)
    val MAX_DATE: LocalDate = MAX_MONTH.atEndOfMonth()

    enum class Phase {
        NEW,
        WAXING_CRESCENT,
        FIRST_QUARTER,
        WAXING_GIBBOUS,
        FULL,
        WANING_GIBBOUS,
        LAST_QUARTER,
        WANING_CRESCENT,
    }

    enum class Zodiac { ARIES, TAURUS, GEMINI, CANCER, LEO, VIRGO, LIBRA, SCORPIO, SAGITTARIUS, CAPRICORN, AQUARIUS, PISCES }

    enum class Rating { GOOD, NEUTRAL, BAD }

    enum class Task { PLANTING, REPOTTING, FEEDING, WATERING, PRUNING }

    /** Everything the UI needs for one calendar day. */
    data class DayInfo(
        val date: LocalDate,
        val phase: Phase,
        val zodiac: Zodiac,
        /** 0.0 = new, 1.0 = full. */
        val illumination: Double,
        val isWaxing: Boolean,
        val rating: Rating,
        val favourable: List<Task>,
        val avoid: List<Task>,
    )

    // ── Astronomy ────────────────────────────────────────────────

    /** Julian Day at 12:00 UT on [date]. */
    private fun julianDay(date: LocalDate): Double = date.toEpochDay() + 2440588.0

    /** Days elapsed since the last new moon, 0 until [SYNODIC_MONTH]. */
    fun moonAge(date: LocalDate): Double {
        val age = (julianDay(date) - NEW_MOON_EPOCH_JD) % SYNODIC_MONTH
        return if (age < 0) age + SYNODIC_MONTH else age
    }

    fun illumination(date: LocalDate): Double =
        (1 - cos(2 * Math.PI * moonAge(date) / SYNODIC_MONTH)) / 2

    fun isWaxing(date: LocalDate): Boolean = moonAge(date) < SYNODIC_MONTH / 2

    /** Normalized position in the synodic month: 0 = new, 0.5 = full, →1 = new again. */
    fun cyclePosition(date: LocalDate): Double = moonAge(date) / SYNODIC_MONTH

    fun phase(date: LocalDate): Phase {
        val age = moonAge(date)
        // Symmetric about conjunction. The day BEFORE the new moon is as much
        // "new" as the day after it, and [isTurningPoint] already rates both as
        // the do-nothing window — labelling that tail WANING_CRESCENT made the
        // card contradict its own rating once per cycle.
        if (age >= SYNODIC_MONTH - 1.0) return Phase.NEW
        return when (age) {
            in 0.0..1.0 -> Phase.NEW
            in 1.0..6.4 -> Phase.WAXING_CRESCENT
            in 6.4..8.4 -> Phase.FIRST_QUARTER
            in 8.4..13.8 -> Phase.WAXING_GIBBOUS
            in 13.8..15.8 -> Phase.FULL
            in 15.8..21.1 -> Phase.WANING_GIBBOUS
            in 21.1..23.1 -> Phase.LAST_QUARTER
            else -> Phase.WANING_CRESCENT
        }
    }

    /** Apparent ecliptic longitude of the Moon in degrees, 0..360. */
    fun moonLongitude(date: LocalDate): Double {
        val d = julianDay(date) - J2000_JD
        val meanLongitude = 218.316 + 13.176396 * d
        val meanAnomaly = 134.963 + 13.064993 * d
        val elongation = 297.850 + 12.190749 * d
        val sunAnomaly = 357.529 + 0.98560028 * d
        // Standard four-term series. The evection argument is (M - 2D), NOT
        // (2D - M): sin is odd, so writing it the other way round flips the
        // term's sign and swings the result by up to 2 * 1.274 = 2.5°, which is
        // enough to land on the wrong side of a 30° zodiac boundary.
        val lambda = meanLongitude +
            6.289 * sin(Math.toRadians(meanAnomaly)) -
            1.274 * sin(Math.toRadians(meanAnomaly - 2 * elongation)) +
            0.658 * sin(Math.toRadians(2 * elongation)) -
            0.186 * sin(Math.toRadians(sunAnomaly))
        val normalised = lambda % 360
        return if (normalised < 0) normalised + 360 else normalised
    }

    fun zodiac(date: LocalDate): Zodiac =
        Zodiac.entries[floor(moonLongitude(date) / 30.0).toInt().coerceIn(0, 11)]

    // ── Navigation bounds ───────────────────────────────────────────

    fun clampMonth(m: YearMonth): YearMonth = minOf(maxOf(m, MIN_MONTH), MAX_MONTH)

    fun clampDate(d: LocalDate): LocalDate = when {
        d.isBefore(MIN_DATE) -> MIN_DATE
        d.isAfter(MAX_DATE) -> MAX_DATE
        else -> d
    }

    fun isInRange(d: LocalDate): Boolean = !d.isBefore(MIN_DATE) && !d.isAfter(MAX_DATE)

    // ── Gardening rules ──────────────────────────────────────────

    private data class Rules(
        val goodSigns: Set<Zodiac>,
        val badSigns: Set<Zodiac>,
        val waxingTasks: List<Task>,
        val waningTasks: List<Task>,
        val avoidWaterOnWaterSigns: Boolean,
    )

    private val WATER_SIGNS = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES)

    private val RULES: Map<PlantCategory, Rules> = mapOf(
        PlantCategory.FRUITING to Rules(
            goodSigns = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES, Zodiac.LIBRA, Zodiac.TAURUS),
            badSigns = setOf(Zodiac.LEO, Zodiac.AQUARIUS, Zodiac.ARIES),
            waxingTasks = listOf(Task.PLANTING, Task.FEEDING, Task.REPOTTING),
            waningTasks = listOf(Task.PRUNING, Task.REPOTTING),
            avoidWaterOnWaterSigns = false,
        ),
        PlantCategory.ROOT to Rules(
            goodSigns = setOf(Zodiac.TAURUS, Zodiac.CAPRICORN, Zodiac.VIRGO, Zodiac.SCORPIO),
            badSigns = setOf(Zodiac.LEO, Zodiac.AQUARIUS, Zodiac.GEMINI),
            waxingTasks = listOf(Task.FEEDING),
            waningTasks = listOf(Task.PLANTING, Task.REPOTTING, Task.PRUNING),
            avoidWaterOnWaterSigns = false,
        ),
        PlantCategory.LEAFY to Rules(
            goodSigns = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES, Zodiac.LIBRA),
            badSigns = setOf(Zodiac.LEO, Zodiac.ARIES, Zodiac.AQUARIUS),
            waxingTasks = listOf(Task.PLANTING, Task.FEEDING, Task.WATERING),
            waningTasks = listOf(Task.PRUNING),
            avoidWaterOnWaterSigns = false,
        ),
        PlantCategory.FLOWERING to Rules(
            goodSigns = setOf(Zodiac.LIBRA, Zodiac.CANCER, Zodiac.PISCES, Zodiac.TAURUS, Zodiac.VIRGO),
            badSigns = setOf(Zodiac.LEO, Zodiac.ARIES, Zodiac.AQUARIUS),
            waxingTasks = listOf(Task.PLANTING, Task.FEEDING, Task.REPOTTING),
            waningTasks = listOf(Task.PRUNING, Task.REPOTTING),
            avoidWaterOnWaterSigns = false,
        ),
        PlantCategory.SUCCULENT to Rules(
            goodSigns = setOf(Zodiac.CAPRICORN, Zodiac.TAURUS, Zodiac.VIRGO, Zodiac.SAGITTARIUS, Zodiac.LEO),
            badSigns = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES),
            waxingTasks = listOf(Task.REPOTTING, Task.PLANTING),
            waningTasks = listOf(Task.PRUNING, Task.REPOTTING),
            avoidWaterOnWaterSigns = true,
        ),
        PlantCategory.FOLIAGE to Rules(
            goodSigns = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES, Zodiac.VIRGO, Zodiac.LIBRA),
            badSigns = setOf(Zodiac.LEO, Zodiac.ARIES, Zodiac.AQUARIUS),
            waxingTasks = listOf(Task.REPOTTING, Task.FEEDING, Task.PLANTING),
            waningTasks = listOf(Task.PRUNING, Task.REPOTTING),
            avoidWaterOnWaterSigns = false,
        ),
        PlantCategory.HERB to Rules(
            goodSigns = setOf(Zodiac.CANCER, Zodiac.PISCES, Zodiac.SCORPIO, Zodiac.GEMINI, Zodiac.LIBRA),
            badSigns = setOf(Zodiac.ARIES, Zodiac.LEO, Zodiac.CAPRICORN),
            waxingTasks = listOf(Task.PLANTING, Task.FEEDING, Task.WATERING),
            waningTasks = listOf(Task.PRUNING),
            avoidWaterOnWaterSigns = false,
        ),
    )

    private fun rules(category: PlantCategory): Rules = RULES.getValue(category)

    /** New/full moon days: the classic "do nothing" window. */
    private fun isTurningPoint(date: LocalDate): Boolean {
        val age = moonAge(date)
        return age < 1.0 ||
            age > SYNODIC_MONTH - 1.0 ||
            (age > SYNODIC_MONTH / 2 - 1.0 && age < SYNODIC_MONTH / 2 + 1.0)
    }

    fun rating(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): Rating = when {
        isTurningPoint(date) -> Rating.BAD
        zodiac(date) in rules(category).badSigns -> Rating.BAD
        zodiac(date) in rules(category).goodSigns -> Rating.GOOD
        else -> Rating.NEUTRAL
    }

    /**
     * Tasks the day suits, most-recommended first, capped at three so the row
     * in the UI never wraps. Rules vary by [category]: waxing moon generally
     * favours above-ground growth work, waning favours pruning/root work, and
     * water-sign days favour watering — except for succulents, where a water
     * sign is a day to actively hold off watering.
     */
    fun favourableTasks(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): List<Task> {
        if (isTurningPoint(date)) return emptyList()
        val r = rules(category)
        val sign = zodiac(date)
        val waxing = isWaxing(date)
        if (sign in r.badSigns) return if (waxing) emptyList() else listOf(Task.PRUNING)
        val base = if (waxing) r.waxingTasks else r.waningTasks
        val water = if (sign in WATER_SIGNS && !r.avoidWaterOnWaterSigns) listOf(Task.WATERING) else emptyList()
        // Water first, not last: the cap is three, and several categories
        // (FOLIAGE, FRUITING, FLOWERING) already list three waxing tasks, so
        // appending WATERING meant take(3) always discarded it — the one task
        // the water sign is actually signalling.
        return (water + base).distinct().take(3)
    }

    /** Tasks to postpone — the complement of [favourableTasks], capped at two. */
    fun avoidTasks(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): List<Task> {
        if (isTurningPoint(date)) return listOf(Task.PLANTING, Task.REPOTTING)
        val r = rules(category)
        val forced = if (r.avoidWaterOnWaterSigns && zodiac(date) in WATER_SIGNS) listOf(Task.WATERING) else emptyList()
        val good = favourableTasks(date, category).toSet()
        return (forced + Task.entries.filter { it !in good && it !in forced }).take(2)
    }

    fun dayInfo(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): DayInfo = DayInfo(
        date = date,
        phase = phase(date),
        zodiac = zodiac(date),
        illumination = illumination(date),
        isWaxing = isWaxing(date),
        rating = rating(date, category),
        favourable = favourableTasks(date, category),
        avoid = avoidTasks(date, category),
    )

    /** Every day of [year]-[month], in order — the calendar grid source. */
    fun month(year: Int, month: Int, category: PlantCategory = PlantCategory.FOLIAGE): List<DayInfo> {
        val first = LocalDate.of(year, month, 1)
        return (0 until first.lengthOfMonth()).map { dayInfo(first.plusDays(it.toLong()), category) }
    }

    /** The next [limit] GOOD days strictly after [from] and within [MAX_DATE] — the "upcoming" list. */
    fun upcomingFavourable(
        from: LocalDate,
        limit: Int = 4,
        category: PlantCategory = PlantCategory.FOLIAGE,
    ): List<DayInfo> =
        generateSequence(clampDate(from).plusDays(1)) { it.plusDays(1) }
            .takeWhile { !it.isAfter(MAX_DATE) }
            .take(90)
            .map { dayInfo(it, category) }
            .filter { it.rating == Rating.GOOD }
            .take(limit)
            .toList()
}
