package com.plantdiseases.app.ui.common

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.time.Instant
import java.time.ZoneOffset

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class MeteredResetLineTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()
    private val now = Instant.parse("2026-09-01T18:48:00Z")

    @Test
    @Config(qualifiers = "en")
    fun `time left and local reset time, in English`() {
        val line = formatMeteredResetLine(context, "2026-09-02T00:00:00Z", now, ZoneOffset.UTC)
        assertEquals("Resets in 5 h 12 min, at 12:00 AM", line?.replace(" AM", " AM"))
    }

    @Test
    @Config(qualifiers = "ru")
    fun `time left and local reset time, in Russian`() {
        val line = formatMeteredResetLine(context, "2026-09-02T00:00:00Z", now, ZoneOffset.UTC)
        assertEquals("Обновится через 5 ч 12 мин, в 00:00", line)
    }

    @Test
    @Config(qualifiers = "en")
    fun `whole hours, minutes and the last minute read naturally`() {
        assertEquals(
            "Resets in 3 h, at 9:48 PM",
            formatMeteredResetLine(context, "2026-09-01T21:48:00Z", now, ZoneOffset.UTC)?.replace(" PM", " PM"),
        )
        assertEquals(
            "Resets in 7 min, at 6:55 PM",
            formatMeteredResetLine(context, "2026-09-01T18:55:00Z", now, ZoneOffset.UTC)?.replace(" PM", " PM"),
        )
        assertEquals(
            "Resets in less than a minute, at 6:48 PM",
            formatMeteredResetLine(context, "2026-09-01T18:48:30Z", now, ZoneOffset.UTC)?.replace(" PM", " PM"),
        )
    }

    @Test
    fun `unusable timestamps leave the line out`() {
        assertNull(formatMeteredResetLine(context, "soon", now, ZoneOffset.UTC))
    }
}
