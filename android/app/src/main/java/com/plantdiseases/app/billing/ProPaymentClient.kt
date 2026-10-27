package com.plantdiseases.app.billing

import android.content.Intent

data class RuStorePayConfiguration(
    val consoleAppId: String,
    val monthlyProductId: String,
    val yearlyProductId: String,
    val enabled: Boolean,
) {
    val productIds: List<String>
        get() = listOf(monthlyProductId, yearlyProductId).filter(String::isNotBlank).distinct()

    val isComplete: Boolean
        get() = enabled && consoleAppId.isNotBlank() && productIds.isNotEmpty()
}

data class ProStoreProduct(
    val productId: String,
    val title: String,
    val description: String,
    /** Already localized and currency-formatted by RuStore. */
    val amountLabel: String,
)

data class ProStorePurchase(
    val productId: String,
    val purchaseId: String,
    val activeHint: Boolean,
    val sandbox: Boolean,
)

enum class PaySheetTheme { LIGHT, DARK }

interface ProPaymentClient {
    val configuration: RuStorePayConfiguration

    fun loadProducts(callback: (Result<List<ProStoreProduct>>) -> Unit)

    fun purchase(
        productId: String,
        externalAccountId: String,
        theme: PaySheetTheme,
        callback: (Result<ProStorePurchase>) -> Unit,
    )

    fun restore(callback: (Result<List<ProStorePurchase>>) -> Unit)

    fun proceedIntent(intent: Intent?, theme: PaySheetTheme)
}

class PayUnavailableException(message: String, cause: Throwable? = null) : IllegalStateException(message, cause)
