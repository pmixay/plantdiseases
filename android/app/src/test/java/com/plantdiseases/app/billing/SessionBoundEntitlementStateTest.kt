package com.plantdiseases.app.billing

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

class SessionBoundEntitlementStateTest {
    @Test
    fun `logout wins when an older refresh later succeeds`() {
        val states = SessionBoundEntitlementState()
        states.bind(SESSION_A)
        states.publish(SESSION_A, EntitlementState.Ready(activePro()))

        states.clear()

        assertNull(states.beginRefresh(SESSION_A))
        assertFalse(
            states.publish(
                SESSION_A,
                EntitlementState.Ready(activePro().copy(validUntil = "2026-09-30T00:00:00Z")),
            ),
        )
        assertSame(EntitlementState.Free, states.state.value)
        assertNull(EntitlementPolicy.billingSnapshotOrNull(states.state.value))
    }

    @Test
    fun `new session wins when an older refresh later fails`() {
        val states = SessionBoundEntitlementState()
        states.bind(SESSION_A)
        val oldEntitlement = activePro()
        states.publish(SESSION_A, EntitlementState.Ready(oldEntitlement))
        val refresh = requireNotNull(states.beginRefresh(SESSION_A))

        states.bind(SESSION_B)

        assertFalse(
            states.publish(
                SESSION_A,
                EntitlementState.Unavailable(refresh.previous),
            ),
        )
        val current = states.state.value as EntitlementState.Checking
        assertNull(current.previous)
        assertFalse(
            EntitlementPolicy.allows(
                current,
                ProFeature.LUNAR_FULL_CALENDAR,
            ),
        )
        assertNull(EntitlementPolicy.billingSnapshotOrNull(current))
    }

    @Test
    fun `current session can refresh before it has a cached snapshot`() {
        val states = SessionBoundEntitlementState()
        states.bind(SESSION_A)

        val refresh = states.beginRefresh(SESSION_A)

        assertNotNull(refresh)
        assertNull(refresh?.previous)
    }

    private fun activePro() = EntitlementSnapshot(
        plan = "pro",
        status = "active",
        validUntil = "2026-09-01T00:00:00Z",
        autoRenewing = true,
        billingAvailable = true,
        billingExternalId = "00000000-0000-4000-8000-000000000010",
        products = listOf("plantdiseases_pro_monthly"),
        quotaPolicyVersion = "launch-2026-08-v1",
        quotas = emptyMap(),
    )

    private companion object {
        const val SESSION_A = "00000000-0000-4000-8000-00000000000a"
        const val SESSION_B = "00000000-0000-4000-8000-00000000000b"
    }
}
