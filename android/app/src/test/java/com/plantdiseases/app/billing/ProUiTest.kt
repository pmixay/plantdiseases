package com.plantdiseases.app.billing

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProUiTest {
    @Test
    fun `pro is not offered in this release`() {
        assertFalse(ProUi.ENABLED)
    }

    @Test
    fun `nothing is locked for a free user while pro is not offered`() {
        ProFeature.entries.forEach { feature ->
            assertTrue(ProUi.allows(EntitlementState.Free, feature))
            assertTrue(ProUi.allows(EntitlementState.Unavailable(), feature))
        }
    }

    @Test
    fun `the entitlement gate applies again once pro is offered`() {
        ProFeature.entries.forEach { feature ->
            assertFalse(ProUi.allows(EntitlementState.Free, feature, offered = true))
        }
    }
}
