package com.plantdiseases.app.ui.pro

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProProductVisibilityTest {
    private val monthly = "plantdiseases_pro_monthly"
    private val yearly = "plantdiseases_pro_yearly"

    @Test
    fun `checkout kill switch hides every product card`() {
        assertFalse(
            shouldShowProProductCard(
                activePro = false,
                productId = monthly,
                allowedProductIds = emptySet(),
                catalogResolved = false,
                loadedProductIds = emptySet(),
            ),
        )
    }

    @Test
    fun `allowed cards can show while the store catalog is loading`() {
        assertTrue(
            shouldShowProProductCard(
                activePro = false,
                productId = monthly,
                allowedProductIds = setOf(monthly, yearly),
                catalogResolved = false,
                loadedProductIds = emptySet(),
            ),
        )
    }

    @Test
    fun `resolved partial catalog hides a missing sku`() {
        val allowed = setOf(monthly, yearly)
        val loaded = setOf(monthly)

        assertTrue(
            shouldShowProProductCard(false, monthly, allowed, true, loaded),
        )
        assertFalse(
            shouldShowProProductCard(false, yearly, allowed, true, loaded),
        )
    }

    @Test
    fun `active pro and blank identifiers never show checkout cards`() {
        assertFalse(
            shouldShowProProductCard(true, monthly, setOf(monthly), false, emptySet()),
        )
        assertFalse(
            shouldShowProProductCard(false, "", setOf(""), false, emptySet()),
        )
    }
}
