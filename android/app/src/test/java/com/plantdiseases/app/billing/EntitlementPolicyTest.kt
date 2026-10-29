package com.plantdiseases.app.billing

import com.plantdiseases.app.data.account.EntitlementQuotaResponse
import com.plantdiseases.app.data.account.EntitlementsResponse
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class EntitlementPolicyTest {
    private val now = Instant.parse("2026-08-26T10:00:00Z")

    @Test
    fun `active trial and grace unlock pro lunar features`() {
        listOf("active", "trial", "grace").forEach { status ->
            val state = EntitlementState.Ready(pro(status, "2026-08-27T10:00:00Z"))
            assertTrue(EntitlementPolicy.allows(state, ProFeature.LUNAR_FULL_CALENDAR, now))
            assertTrue(EntitlementPolicy.allows(state, ProFeature.LUNAR_GARDEN_AGGREGATE, now))
        }
    }

    @Test
    fun `pending expired none and elapsed grace stay locked`() {
        listOf("pending", "expired", "none").forEach { status ->
            assertFalse(
                EntitlementPolicy.allows(
                    EntitlementState.Ready(pro(status, "2026-08-27T10:00:00Z")),
                    ProFeature.LUNAR_FULL_CALENDAR,
                    now,
                ),
            )
        }
        assertFalse(
            EntitlementPolicy.allows(
                EntitlementState.Ready(pro("grace", "2026-08-25T10:00:00Z")),
                ProFeature.LUNAR_FULL_CALENDAR,
                now,
            ),
        )
    }

    @Test
    fun `missing or malformed validity fails closed`() {
        listOf(null, "", "not-an-instant").forEach { validity ->
            assertFalse(
                EntitlementPolicy.allows(
                    EntitlementState.Ready(pro("active", validity)),
                    ProFeature.LUNAR_FULL_CALENDAR,
                    now,
                ),
            )
        }
    }

    @Test
    fun `brief outage keeps only a still valid last known pro result`() {
        assertTrue(
            EntitlementPolicy.allows(
                EntitlementState.Unavailable(pro("active", "2026-08-27T10:00:00Z")),
                ProFeature.LUNAR_FULL_CALENDAR,
                now,
            ),
        )
        assertFalse(
            EntitlementPolicy.allows(
                EntitlementState.Unavailable(pro("active", "2026-08-25T10:00:00Z")),
                ProFeature.LUNAR_FULL_CALENDAR,
                now,
            ),
        )
    }

    @Test
    fun `billing requires fresh ready response and backend availability`() {
        val entitled = pro("active", "2026-08-27T10:00:00Z")
        assertSame(
            entitled,
            EntitlementPolicy.billingSnapshotOrNull(EntitlementState.Ready(entitled)),
        )
        assertNull(
            EntitlementPolicy.billingSnapshotOrNull(EntitlementState.Checking(entitled)),
        )
        assertNull(
            EntitlementPolicy.billingSnapshotOrNull(EntitlementState.Unavailable(entitled)),
        )
        assertNull(
            EntitlementPolicy.billingSnapshotOrNull(
                EntitlementState.Ready(entitled.copy(billingAvailable = false)),
            ),
        )
        assertNull(
            EntitlementPolicy.billingSnapshotOrNull(
                EntitlementState.Ready(entitled.copy(billingExternalId = null)),
            ),
        )
        assertNull(
            EntitlementPolicy.billingSnapshotOrNull(
                EntitlementState.Ready(entitled.copy(products = emptyList())),
            ),
        )
    }

    @Test
    fun `repository snapshot preserves quota policy version and limits`() {
        val snapshot = EntitlementsResponse(
            quotaPolicyVersion = "launch-2026-08-v1",
            quotas = listOf(
                EntitlementQuotaResponse(
                    feature = "gamma_analysis",
                    dailyLimit = 5,
                    remaining = 3,
                    resetAt = "2026-08-27T00:00:00Z",
                ),
            ),
        ).toEntitlementSnapshot()

        assertEquals("launch-2026-08-v1", snapshot.quotaPolicyVersion)
        assertEquals(5, snapshot.quotas.getValue("gamma_analysis").dailyLimit)
    }

    private fun pro(status: String, validUntil: String?) = EntitlementSnapshot(
        plan = "pro",
        status = status,
        validUntil = validUntil,
        autoRenewing = true,
        billingAvailable = true,
        billingExternalId = "00000000-0000-4000-8000-000000000010",
        products = listOf("plantdiseases_pro_monthly"),
        quotaPolicyVersion = "launch-2026-08-v1",
        quotas = emptyMap(),
    )
}
