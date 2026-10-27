package com.plantdiseases.app.billing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RuStorePayConfigurationTest {
    @Test
    fun `blank operator configuration is safely incomplete`() {
        val config = RuStorePayConfiguration("", "", "", enabled = false)
        assertFalse(config.isComplete)
        assertTrue(config.productIds.isEmpty())
    }

    @Test
    fun `one or two configured products are accepted without fallback ids`() {
        val monthly = RuStorePayConfiguration("123", "monthly", "", enabled = true)
        assertTrue(monthly.isComplete)
        assertEquals(listOf("monthly"), monthly.productIds)

        val both = RuStorePayConfiguration("123", "monthly", "yearly", enabled = true)
        assertEquals(listOf("monthly", "yearly"), both.productIds)
    }
}
