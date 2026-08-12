package com.plantdiseases.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import kotlin.math.abs

class LunarCalendarTest {

    @Test
    fun `known new moon epoch date sits within a day of age zero`() {
        // Julian Day is sampled at noon UT while the reference new moon
        // falls at ~14:24 UT the same date, so the raw age can land just
        // under a full synodic month (wrapped) instead of just over zero —
        // check distance from either side of the age-0/age-max seam.
        val synodicMonth = 29.530588853
        val age = LunarCalendar.moonAge(LocalDate.of(2000, 1, 6))
        val distanceFromNew = minOf(age, synodicMonth - age)
        assertTrue(distanceFromNew < 1.0)
    }

    @Test
    fun `age stays inside the synodic month`() {
        var date = LocalDate.of(2024, 1, 1)
        repeat(400) {
            val age = LunarCalendar.moonAge(date)
            assertTrue("age out of range on $date", age >= 0.0 && age < 29.6)
            date = date.plusDays(1)
        }
    }

    @Test
    fun `2026-08-18 is a waxing moon in Scorpio`() {
        val info = LunarCalendar.dayInfo(LocalDate.of(2026, 8, 18))
        assertTrue(info.isWaxing)
        assertEquals(LunarCalendar.Zodiac.SCORPIO, info.zodiac)
    }

    @Test
    fun `every day of a month gets a rating and a month has 28-31 entries`() {
        val days = LunarCalendar.month(2026, 8)
        assertEquals(31, days.size)
        assertTrue(days.all { it.rating in LunarCalendar.Rating.entries })
    }

    @Test
    fun `upcoming favourable days are all good and strictly after today`() {
        val from = LocalDate.of(2026, 8, 18)
        val upcoming = LunarCalendar.upcomingFavourable(from, 4)
        assertEquals(4, upcoming.size)
        assertTrue(upcoming.all { it.rating == LunarCalendar.Rating.GOOD })
        assertTrue(upcoming.all { it.date.isAfter(from) })
    }

    @Test
    fun `upcoming favourable days find 4 within range for every category`() {
        val from = LocalDate.of(2026, 8, 18)
        for (category in PlantCategory.entries) {
            val upcoming = LunarCalendar.upcomingFavourable(from, 4, category)
            assertEquals("category=$category", 4, upcoming.size)
            assertTrue("category=$category", upcoming.all { it.rating == LunarCalendar.Rating.GOOD })
        }
    }

    @Test
    fun `rating differs by category on the same date`() {
        // 2026-08-20 is Sagittarius: a good day for succulents (in their
        // goodSigns set) but only neutral for foliage plants. This is the
        // whole point of making the calendar plant-aware.
        val date = LocalDate.of(2026, 8, 20)
        assertEquals(LunarCalendar.Zodiac.SAGITTARIUS, LunarCalendar.zodiac(date))
        assertEquals(LunarCalendar.Rating.GOOD, LunarCalendar.rating(date, PlantCategory.SUCCULENT))
        assertEquals(LunarCalendar.Rating.NEUTRAL, LunarCalendar.rating(date, PlantCategory.FOLIAGE))
    }

    @Test
    fun `aggregateRating picks the rating shared by the most plants`() {
        // 2026-08-20 (Sagittarius, see the test above): GOOD for SUCCULENT,
        // NEUTRAL for FOLIAGE. Two succulents outvote one foliage plant.
        val date = LocalDate.of(2026, 8, 20)
        val categories = listOf(PlantCategory.SUCCULENT, PlantCategory.SUCCULENT, PlantCategory.FOLIAGE)
        assertEquals(LunarCalendar.Rating.GOOD, LunarCalendar.aggregateRating(date, categories))
    }

    @Test
    fun `aggregateRating breaks an even split toward NEUTRAL`() {
        // Same date, one plant of each category: GOOD and NEUTRAL tie 1-1,
        // so neither wins outright.
        val date = LocalDate.of(2026, 8, 20)
        val categories = listOf(PlantCategory.SUCCULENT, PlantCategory.FOLIAGE)
        assertEquals(LunarCalendar.Rating.NEUTRAL, LunarCalendar.aggregateRating(date, categories))
    }

    @Test
    fun `aggregateRating on an empty garden is NEUTRAL, not a crash`() {
        val date = LocalDate.of(2026, 8, 20)
        assertEquals(LunarCalendar.Rating.NEUTRAL, LunarCalendar.aggregateRating(date, emptyList()))
    }

    @Test
    fun `every day of 2026 and 2027 computes a dayInfo without throwing`() {
        var date = LocalDate.of(2026, 1, 1)
        val end = LocalDate.of(2027, 12, 31)
        var count = 0
        while (!date.isAfter(end)) {
            for (category in PlantCategory.entries) {
                val info = LunarCalendar.dayInfo(date, category)
                assertTrue(info.rating in LunarCalendar.Rating.entries)
            }
            count++
            date = date.plusDays(1)
        }
        assertEquals(730, count)
    }

    @Test
    fun `clampMonth and clampDate stay within the navigable range`() {
        assertEquals(LunarCalendar.MIN_MONTH, LunarCalendar.clampMonth(LunarCalendar.MIN_MONTH.minusMonths(1)))
        assertEquals(LunarCalendar.MAX_MONTH, LunarCalendar.clampMonth(LunarCalendar.MAX_MONTH.plusMonths(1)))
        assertEquals(LunarCalendar.MIN_DATE, LunarCalendar.clampDate(LunarCalendar.MIN_DATE.minusDays(1)))
        assertEquals(LunarCalendar.MAX_DATE, LunarCalendar.clampDate(LunarCalendar.MAX_DATE.plusDays(1)))
        assertTrue(LunarCalendar.isInRange(LocalDate.of(2026, 8, 18)))
        assertTrue(!LunarCalendar.isInRange(LunarCalendar.MIN_DATE.minusDays(1)))
        assertTrue(!LunarCalendar.isInRange(LunarCalendar.MAX_DATE.plusDays(1)))
    }

    @Test
    fun `navigable window does not dead-end within the life of a build`() {
        // A two-year window expires while the installed APK is still in use:
        // the user simply cannot page forward. Assert real headroom either side
        // rather than the exact literals, so widening it again never fails here.
        assertTrue(LunarCalendar.MAX_MONTH.year - LunarCalendar.MIN_MONTH.year >= 10)
        assertTrue(LunarCalendar.isInRange(LocalDate.of(2026, 8, 5).plusYears(5)))
    }

    @Test
    fun `moon longitude matches a high-precision ephemeris`() {
        // Reference values from a 19-term Meeus (Astronomical Algorithms ch.47)
        // expansion. The four-term series here is documented as good to ~0.3°,
        // so 0.5° is a tolerant band that still fails hard on a wrong term.
        //
        // This is the assertion that pins the evection term's SIGN. Writing it
        // as sin(2D - M') instead of -sin(M' - 2D) shifts the result by up to
        // 2.5°, which every date below would then miss by an order of magnitude
        // more than the tolerance. Without this test that error is invisible:
        // the zodiac assertions elsewhere happen to agree either way.
        val references = mapOf(
            LocalDate.of(2026, 1, 15) to 258.066,
            LocalDate.of(2026, 12, 31) to 198.251,
            LocalDate.of(2027, 6, 10) to 157.035,
            LocalDate.of(2030, 3, 21) to 204.975,
        )
        for ((date, expected) in references) {
            val actual = LunarCalendar.moonLongitude(date)
            val delta = abs(((actual - expected + 180.0) % 360.0) - 180.0)
            assertTrue("$date: expected ~$expected, got $actual (off by $delta°)", delta < 0.5)
        }
    }

    @Test
    fun `moon longitude stays normalised across the whole navigable window`() {
        var date = LunarCalendar.MIN_DATE
        while (!date.isAfter(LunarCalendar.MAX_DATE)) {
            val lon = LunarCalendar.moonLongitude(date)
            assertTrue("longitude out of range on $date: $lon", lon >= 0.0 && lon < 360.0)
            date = date.plusDays(29)
        }
    }

    @Test
    fun `the day before conjunction is labelled new, not waning crescent`() {
        // phase() and isTurningPoint() have to agree: a day rated "do nothing"
        // because it sits on the new moon must not be captioned as a crescent.
        var date = LocalDate.of(2026, 1, 1)
        var checked = 0
        while (!date.isAfter(LocalDate.of(2027, 12, 31))) {
            val age = LunarCalendar.moonAge(date)
            if (age >= 28.6) {
                assertEquals("$date (age $age)", LunarCalendar.Phase.NEW, LunarCalendar.phase(date))
                checked++
            }
            date = date.plusDays(1)
        }
        assertTrue("expected some pre-conjunction days in the window", checked > 0)
    }

    @Test
    fun `water sign days recommend watering for the default foliage category`() {
        // FOLIAGE lists three waxing tasks, so appending WATERING and then
        // capping at three silently dropped the very task the water sign is
        // signalling. Find a waxing water-sign day that is not a turning point
        // and assert watering actually surfaces.
        var date = LunarCalendar.MIN_DATE
        var asserted = false
        while (!date.isAfter(LocalDate.of(2027, 12, 31))) {
            val info = LunarCalendar.dayInfo(date, PlantCategory.FOLIAGE)
            val waterSign = info.zodiac in setOf(
                LunarCalendar.Zodiac.CANCER,
                LunarCalendar.Zodiac.SCORPIO,
                LunarCalendar.Zodiac.PISCES,
            )
            if (waterSign && info.favourable.isNotEmpty()) {
                assertTrue(
                    "$date (${info.zodiac}) should favour watering, got ${info.favourable}",
                    LunarCalendar.Task.WATERING in info.favourable,
                )
                asserted = true
            }
            date = date.plusDays(1)
        }
        assertTrue("expected at least one favourable water-sign day", asserted)
    }

    @Test
    fun `cyclePosition is near zero at new moon and near half at full moon`() {
        // 2000-01-06 is the reference new-moon epoch date (see the epoch test
        // above); 2026-08-28 has moon age ~15.34, close to a full moon.
        val nearNew = LunarCalendar.cyclePosition(LocalDate.of(2000, 1, 7))
        assertTrue(nearNew < 0.05 || nearNew > 0.95)
        val nearFull = LunarCalendar.cyclePosition(LocalDate.of(2026, 8, 28))
        assertTrue(nearFull in 0.45..0.55)
    }
}
