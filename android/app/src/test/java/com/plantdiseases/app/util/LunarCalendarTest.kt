package com.plantdiseases.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

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
        assertTrue(!LunarCalendar.isInRange(LocalDate.of(2025, 12, 31)))
        assertTrue(!LunarCalendar.isInRange(LocalDate.of(2028, 1, 1)))
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
