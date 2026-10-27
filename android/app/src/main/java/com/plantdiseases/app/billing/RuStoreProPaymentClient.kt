package com.plantdiseases.app.billing

import android.content.Intent
import ru.rustore.sdk.pay.RuStorePayClient
import ru.rustore.sdk.pay.model.AppUserId
import ru.rustore.sdk.pay.model.PreferredPurchaseType
import ru.rustore.sdk.pay.model.ProductId
import ru.rustore.sdk.pay.model.ProductPurchaseParams
import ru.rustore.sdk.pay.model.ProductType
import ru.rustore.sdk.pay.model.PurchaseAvailabilityResult
import ru.rustore.sdk.pay.model.SdkTheme
import ru.rustore.sdk.pay.model.SubscriptionPurchase
import ru.rustore.sdk.pay.model.SubscriptionPurchaseStatus

/** Thin optional-integration adapter over RuStore Pay SDK 11.1.0. */
class RuStoreProPaymentClient(
    override val configuration: RuStorePayConfiguration,
) : ProPaymentClient {

    override fun loadProducts(callback: (Result<List<ProStoreProduct>>) -> Unit) {
        if (!configuration.isComplete) return callback(unconfigured())
        sdkCall(callback) { client ->
            client.getPurchaseInteractor().getPurchaseAvailability()
                .addOnSuccessListener { availability ->
                    when (availability) {
                        PurchaseAvailabilityResult.Available -> loadAvailableProducts(client, callback)
                        is PurchaseAvailabilityResult.Unavailable -> callback(
                            Result.failure(
                                PayUnavailableException(
                                    "RuStore purchases are unavailable",
                                    availability.cause,
                                ),
                            ),
                        )
                    }
                }
                .addOnFailureListener { callback(Result.failure(PayUnavailableException("RuStore unavailable", it))) }
        }
    }

    private fun loadAvailableProducts(
        client: RuStorePayClient,
        callback: (Result<List<ProStoreProduct>>) -> Unit,
    ) {
        client.getProductInteractor().getProducts(configuration.productIds.map(::ProductId))
            .addOnSuccessListener { products ->
                callback(
                    Result.success(
                        products.map { product ->
                            ProStoreProduct(
                                productId = product.productId.value,
                                title = product.title.value,
                                description = product.description?.value.orEmpty(),
                                amountLabel = product.amountLabel.value,
                            )
                        },
                    ),
                )
            }
            .addOnFailureListener { callback(Result.failure(PayUnavailableException("Products unavailable", it))) }
    }

    override fun purchase(
        productId: String,
        externalAccountId: String,
        theme: PaySheetTheme,
        callback: (Result<ProStorePurchase>) -> Unit,
    ) {
        if (!configuration.isComplete || productId !in configuration.productIds) {
            return callback(unconfigured())
        }
        if (externalAccountId.isBlank()) {
            return callback(Result.failure(PayUnavailableException("Missing server purchase identity")))
        }
        sdkCall(callback) { client ->
            client.getPurchaseInteractor().purchase(
                params = ProductPurchaseParams(
                    productId = ProductId(productId),
                    appUserId = AppUserId(externalAccountId),
                ),
                preferredPurchaseType = PreferredPurchaseType.ONE_STEP,
                sdkTheme = theme.toSdkTheme(),
            ).addOnSuccessListener { result ->
                callback(
                    Result.success(
                        ProStorePurchase(
                            productId = result.productId.value,
                            purchaseId = result.purchaseId.value,
                            activeHint = true,
                            sandbox = result.sandbox,
                        ),
                    ),
                )
            }.addOnFailureListener { callback(Result.failure(it)) }
        }
    }

    override fun restore(callback: (Result<List<ProStorePurchase>>) -> Unit) {
        if (!configuration.isComplete) return callback(unconfigured())
        sdkCall(callback) { client ->
            client.getPurchaseInteractor().getPurchases(productType = ProductType.SUBSCRIPTION)
                .addOnSuccessListener { purchases ->
                    callback(
                        Result.success(
                            purchases.filterIsInstance<SubscriptionPurchase>()
                                .filter { purchase ->
                                    purchase.productId.value in configuration.productIds
                                }
                                .map { purchase ->
                                    ProStorePurchase(
                                        productId = purchase.productId.value,
                                        purchaseId = purchase.purchaseId.value,
                                        activeHint = purchase.status in ACTIVE_SUBSCRIPTION_STATUSES,
                                        sandbox = purchase.sandbox,
                                    )
                                },
                        ),
                    )
                }
                .addOnFailureListener { callback(Result.failure(PayUnavailableException("Restore unavailable", it))) }
        }
    }

    override fun proceedIntent(intent: Intent?, theme: PaySheetTheme) {
        if (!configuration.isComplete || intent == null) return
        runCatching {
            RuStorePayClient.instance.getIntentInteractor().proceedIntent(intent, theme.toSdkTheme())
        }
    }

    private fun <T> sdkCall(
        callback: (Result<T>) -> Unit,
        block: (RuStorePayClient) -> Unit,
    ) {
        runCatching { block(RuStorePayClient.instance) }
            .onFailure { callback(Result.failure(PayUnavailableException("RuStore Pay is unavailable", it))) }
    }

    private fun <T> unconfigured(): Result<T> =
        Result.failure(PayUnavailableException("RuStore Pay is not configured"))

    private fun PaySheetTheme.toSdkTheme(): SdkTheme = when (this) {
        PaySheetTheme.LIGHT -> SdkTheme.LIGHT
        PaySheetTheme.DARK -> SdkTheme.DARK
    }

    private companion object {
        val ACTIVE_SUBSCRIPTION_STATUSES = setOf(
            SubscriptionPurchaseStatus.ACTIVE,
            SubscriptionPurchaseStatus.PAUSED,
        )
    }
}
