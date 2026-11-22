package com.plantdiseases.app.ui.common

import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.ScanRepository
import java.time.ZoneId
import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MeteredAccessUiTest {
    @Test
    fun `free quota exhaustion keeps authoritative details and offers pro`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.QuotaExhaustedException(
                    plan = "free",
                    limit = 5,
                    remaining = 0,
                    resetAt = "2026-09-01T00:00:00Z",
                ),
                proOffered = true,
            ),
        )

        assertTrue(policy.offerPro)
        assertEquals(R.string.metered_quota_free_body, policy.message)
        assertEquals(0, policy.remaining)
        assertEquals("2026-09-01T00:00:00Z", policy.resetAt)
    }

    @Test
    fun `pro quota exhaustion never offers another upgrade`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.QuotaExhaustedException(
                    plan = " PRO ",
                    limit = 30,
                    remaining = 0,
                    resetAt = "2026-09-01T00:00:00Z",
                ),
                proOffered = true,
            ),
        )

        assertFalse(policy.offerPro)
        assertEquals(R.string.metered_quota_pro_body, policy.message)
    }

    @Test
    fun `pro-required response also avoids upgrade when server says pro is active`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.ProRequiredException(
                    plan = "pro",
                    limit = null,
                    remaining = null,
                    resetAt = null,
                ),
                proOffered = true,
            ),
        )

        assertFalse(policy.offerPro)
        assertEquals(R.string.metered_pro_active_unavailable_title, policy.title)
        assertEquals(R.string.metered_pro_required_active_body, policy.message)
    }

    @Test
    fun `negative remaining values are not rendered`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.ProRequiredException(
                    plan = "free",
                    limit = 0,
                    remaining = -1,
                    resetAt = " ",
                ),
                proOffered = true,
            ),
        )

        assertTrue(policy.offerPro)
        assertNull(policy.remaining)
        assertNull(policy.resetAt)
    }

    @Test
    fun `quota exhaustion never mentions pro while pro is not offered`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.QuotaExhaustedException(
                    plan = "free",
                    limit = 1,
                    remaining = 0,
                    resetAt = "2026-09-01T00:00:00Z",
                ),
            ),
        )

        assertFalse(policy.offerPro)
        assertEquals(R.string.metered_quota_title, policy.title)
        assertEquals(R.string.metered_quota_body, policy.message)
        assertEquals("2026-09-01T00:00:00Z", policy.resetAt)
    }

    @Test
    fun `a zero allowance reads as unavailable while pro is not offered`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.ProRequiredException(
                    plan = "free",
                    limit = 0,
                    remaining = 0,
                    resetAt = null,
                ),
            ),
        )

        assertFalse(policy.offerPro)
        assertEquals(R.string.metered_pro_active_unavailable_title, policy.title)
        assertEquals(R.string.metered_unavailable_body, policy.message)
        // Nothing to wait for: a lock, no reset line, and a way to sign in.
        assertEquals(R.drawable.ic_lock, policy.icon)
        assertNull(policy.resetAt)
        assertTrue(policy.offerSignIn)
    }

    @Test
    fun `quota exhaustion shows a clock and no sign-in offer`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.QuotaExhaustedException(
                    plan = "free",
                    limit = 10,
                    remaining = 0,
                    resetAt = "2026-09-01T00:00:00Z",
                ),
                proOffered = false,
            ),
        )

        assertEquals(R.drawable.ic_clock, policy.icon)
        assertFalse(policy.offerSignIn)
    }

    @Test
    fun `each feature names the allowance that ran out`() {
        val expected = mapOf(
            ScanRepository.MeteredFeature.ANALYSIS to
                Triple(R.string.metered_quota_title_analysis, R.string.metered_quota_body_analysis, R.drawable.ic_scan),
            ScanRepository.MeteredFeature.FRONTIER_ANALYSIS to
                Triple(R.string.metered_quota_title_frontier, R.string.metered_quota_body_frontier, R.drawable.ic_sparkle),
            ScanRepository.MeteredFeature.IDENTIFICATION to
                Triple(R.string.metered_quota_title_identify, R.string.metered_quota_body_identify, R.drawable.ic_search),
            ScanRepository.MeteredFeature.CHAT to
                Triple(R.string.metered_quota_title_chat, R.string.metered_quota_body_chat, R.drawable.ic_doctor_chat),
            ScanRepository.MeteredFeature.CARE_PLAN to
                Triple(R.string.metered_quota_title_care_plan, R.string.metered_quota_body_care_plan, R.drawable.ic_care),
        )
        expected.forEach { (feature, copy) ->
            val policy = requireNotNull(
                meteredDialogPolicy(
                    ScanRepository.QuotaExhaustedException("free", 5, 0, "2026-09-01T00:00:00Z", feature),
                ),
            )
            assertEquals(feature.name, copy.first, policy.title)
            assertEquals(feature.name, copy.second, policy.message)
            assertEquals(feature.name, copy.third, policy.icon)
        }
    }

    @Test
    fun `a guest who runs out is told signing in gives more`() {
        val policy = requireNotNull(
            meteredDialogPolicy(
                ScanRepository.QuotaExhaustedException(
                    plan = "free",
                    limit = 5,
                    remaining = 0,
                    resetAt = "2026-09-01T00:00:00Z",
                    feature = ScanRepository.MeteredFeature.CHAT,
                    guest = true,
                ),
            ),
        )

        assertTrue(policy.offerSignIn)
        assertEquals(R.string.metered_quota_guest_hint, policy.hint)
    }

    @Test
    fun `reset timestamp is localized in the device zone`() {
        val raw = "2026-09-01T00:30:00Z"
        val zone = ZoneId.of("Europe/Moscow")

        val english = formatMeteredResetAt(raw, Locale.US, zone)
        val russian = formatMeteredResetAt(raw, Locale.forLanguageTag("ru-RU"), zone)

        assertNotNull(english)
        assertNotNull(russian)
        assertTrue(english.orEmpty().contains("2026"))
        assertTrue(russian.orEmpty().contains("2026"))
        assertNotEquals(english, russian)
    }

    @Test
    fun `malformed reset timestamp fails safely`() {
        assertNull(
            formatMeteredResetAt(
                "not-a-server-timestamp",
                Locale.US,
                ZoneId.of("UTC"),
            ),
        )
    }
}
