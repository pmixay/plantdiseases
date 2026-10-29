package com.plantdiseases.app.ui.pro

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.net.Uri
import android.os.Bundle
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.billing.EntitlementPolicy
import com.plantdiseases.app.billing.EntitlementRepository
import com.plantdiseases.app.billing.EntitlementSnapshot
import com.plantdiseases.app.billing.EntitlementState
import com.plantdiseases.app.billing.PaySheetTheme
import com.plantdiseases.app.billing.ProPaymentClient
import com.plantdiseases.app.billing.ProStoreProduct
import com.plantdiseases.app.billing.ProStorePurchase
import com.plantdiseases.app.databinding.ActivityProBinding
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import java.text.DateFormat
import java.time.Instant
import java.util.Date
import javax.inject.Inject
import kotlinx.coroutines.launch

@AndroidEntryPoint
class ProActivity : AppCompatActivity() {
    @Inject
    lateinit var authRepository: AuthRepository

    @Inject
    lateinit var entitlementRepository: EntitlementRepository

    @Inject
    lateinit var paymentClient: ProPaymentClient

    private lateinit var binding: ActivityProBinding
    private var products: Map<String, ProStoreProduct> = emptyMap()

    /** The exact server allowlist for which the RuStore catalog was resolved. */
    private var resolvedCatalogFor: Set<String>? = null
    private var productsLoading = false
    private var entitlementState: EntitlementState = EntitlementState.Checking()
    private var busy = false

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityProBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.btnSignIn.setOnClickListener { startActivity(AccountActivity.intent(this)) }
        binding.btnMonthly.setOnClickListener {
            startPurchase(paymentClient.configuration.monthlyProductId)
        }
        binding.btnYearly.setOnClickListener {
            startPurchase(paymentClient.configuration.yearlyProductId)
        }
        binding.btnRestore.setOnClickListener { restorePurchase() }
        binding.btnRefresh.setOnClickListener { refreshEntitlement() }
        binding.btnManageSubscription.setOnClickListener { openSubscriptionManagement() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                entitlementRepository.state.collect { state ->
                    entitlementState = state
                    renderEntitlement()
                    loadProductsWhenVerified()
                }
            }
        }
        if (savedInstanceState == null) paymentClient.proceedIntent(intent, payTheme())
    }

    override fun onResume() {
        super.onResume()
        entitlementRepository.refreshInBackground()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        paymentClient.proceedIntent(intent, payTheme())
        entitlementRepository.refreshInBackground()
    }

    private fun loadProductsWhenVerified() {
        if (EntitlementPolicy.isActivePro(entitlementState)) return
        val snapshot = billingReadySnapshot()
        if (snapshot == null) {
            invalidateProductCatalog()
            renderProductChoices()
            return
        }
        val allowedProductIds = allowedProductIds(snapshot)
        if (allowedProductIds.isEmpty()) {
            invalidateProductCatalog()
            renderProductChoices()
            return
        }
        if (productsLoading || resolvedCatalogFor == allowedProductIds) return
        productsLoading = true
        setBusy(true)
        paymentClient.loadProducts { result ->
            onUi {
                productsLoading = false
                setBusy(false)
                result.onSuccess { storeProducts ->
                    products = storeProducts
                        .filter {
                            it.productId in allowedProductIds && it.amountLabel.isNotBlank()
                        }
                        .associateBy(ProStoreProduct::productId)
                    resolvedCatalogFor = allowedProductIds
                    bindProduct(
                        paymentClient.configuration.monthlyProductId,
                        binding.tvMonthlyDescription,
                        binding.tvMonthlyPrice,
                    )
                    bindProduct(
                        paymentClient.configuration.yearlyProductId,
                        binding.tvYearlyDescription,
                        binding.tvYearlyPrice,
                    )
                    renderProductChoices()
                    if (products.isEmpty()) {
                        showProductFailure(R.string.pro_pay_unavailable, allowedProductIds)
                    } else {
                        updateActions()
                    }
                }.onFailure {
                    showProductFailure(R.string.pro_pay_unavailable, allowedProductIds)
                }
            }
        }
    }

    private fun bindProduct(productId: String, description: TextView, price: TextView) {
        val product = products[productId]
        description.text = product?.description.orEmpty()
        description.isVisible = !product?.description.isNullOrBlank()
        // Never synthesize or hardcode a price: amountLabel is already
        // localized and currency-formatted by RuStore.
        price.text = product?.amountLabel ?: getString(R.string.pro_price_unavailable)
    }

    private fun startPurchase(productId: String) {
        if (busy || EntitlementPolicy.isActivePro(entitlementRepository.state.value)) return
        val authenticated = authRepository.state.value as? AuthState.Authenticated
        if (authenticated == null) {
            showMessage(R.string.pro_sign_in_required)
            binding.btnSignIn.isVisible = true
            return
        }
        val snapshot = billingReadySnapshot()
        val externalId = snapshot?.billingExternalId
        if (
            snapshot == null ||
            externalId.isNullOrBlank() ||
            productId !in allowedProductIds(snapshot) ||
            productId !in products
        ) {
            showMessage(R.string.pro_verification_unavailable)
            return
        }
        setBusy(true)
        paymentClient.purchase(productId, externalId, payTheme()) { result ->
            onUi {
                result.onSuccess(::verifyPurchase)
                    .onFailure {
                        setBusy(false)
                        showMessage(R.string.pro_purchase_failed)
                    }
            }
        }
    }

    private fun verifyPurchase(purchase: ProStorePurchase) {
        lifecycleScope.launch {
            entitlementRepository.verifyRustorePurchase(
                productId = purchase.productId,
                purchaseId = purchase.purchaseId,
                sandbox = purchase.sandbox,
            ).onSuccess { snapshot ->
                showMessage(
                    if (EntitlementPolicy.isActivePro(EntitlementState.Ready(snapshot))) {
                        R.string.pro_purchase_verified
                    } else {
                        R.string.pro_purchase_pending
                    },
                )
            }.onFailure {
                showMessage(R.string.pro_verification_unavailable)
            }
            setBusy(false)
        }
    }

    private fun restorePurchase() {
        if (authRepository.state.value !is AuthState.Authenticated) {
            showMessage(R.string.pro_sign_in_required)
            binding.btnSignIn.isVisible = true
            return
        }
        val snapshot = billingReadySnapshot()
        if (snapshot == null) {
            showMessage(R.string.pro_verification_unavailable)
            return
        }
        val allowedProductIds = allowedProductIds(snapshot)
        if (allowedProductIds.isEmpty()) {
            showMessage(R.string.pro_verification_unavailable)
            return
        }
        setBusy(true)
        paymentClient.restore { result ->
            onUi {
                result.onSuccess { purchases ->
                    val candidates = purchases
                        .filter { it.productId in allowedProductIds }
                        .sortedByDescending(ProStorePurchase::activeHint)
                    if (candidates.isEmpty()) {
                        setBusy(false)
                        showMessage(R.string.pro_restore_empty)
                    } else {
                        verifyRestoredPurchases(candidates)
                    }
                }.onFailure {
                    setBusy(false)
                    showMessage(R.string.pro_restore_failed)
                }
            }
        }
    }

    private fun verifyRestoredPurchases(purchases: List<ProStorePurchase>) {
        lifecycleScope.launch {
            val restored = purchases.any { purchase ->
                val snapshot = entitlementRepository.verifyRustorePurchase(
                    productId = purchase.productId,
                    purchaseId = purchase.purchaseId,
                    sandbox = purchase.sandbox,
                ).getOrNull()
                snapshot != null &&
                    EntitlementPolicy.isActivePro(EntitlementState.Ready(snapshot))
            }
            setBusy(false)
            showMessage(if (restored) R.string.pro_restore_success else R.string.pro_restore_failed)
        }
    }

    private fun refreshEntitlement() {
        if (authRepository.state.value !is AuthState.Authenticated) {
            showMessage(R.string.pro_sign_in_required)
            binding.btnSignIn.isVisible = true
            return
        }
        if (busy) return
        invalidateProductCatalog()
        renderProductChoices()
        setBusy(true)
        lifecycleScope.launch {
            entitlementRepository.refresh()
                .onSuccess { showMessage(R.string.pro_refresh_success) }
                .onFailure { showMessage(R.string.pro_verification_unavailable) }
            setBusy(false)
        }
    }

    private fun renderEntitlement() {
        val active = EntitlementPolicy.isActivePro(entitlementState)
        val snapshot = EntitlementPolicy.snapshot(entitlementState)
        binding.tvPlanStatus.text = when {
            active && snapshot?.validUntil != null -> formatActiveUntil(snapshot.validUntil)
            active -> getString(R.string.pro_status_active)
            entitlementState is EntitlementState.Checking -> getString(R.string.pro_status_checking)
            entitlementState is EntitlementState.Unavailable -> getString(R.string.pro_status_unavailable)
            else -> getString(R.string.pro_status_free)
        }
        renderProductChoices(active)
        binding.btnManageSubscription.isVisible = snapshot?.let { entitlement ->
            entitlement.plan.equals("pro", ignoreCase = true) &&
                entitlement.status.lowercase() !in setOf("none", "pending")
        } == true
        updateActions()
    }

    private fun formatActiveUntil(raw: String): String = runCatching {
        val formatted = DateFormat.getDateInstance(DateFormat.MEDIUM).format(Date.from(Instant.parse(raw)))
        getString(R.string.pro_status_active_until, formatted)
    }.getOrDefault(getString(R.string.pro_status_active))

    private fun updateActions() {
        val active = EntitlementPolicy.isActivePro(entitlementState)
        val signedIn = authRepository.state.value is AuthState.Authenticated
        val snapshot = billingReadySnapshot()
        val allowedProducts = snapshot?.let(::allowedProductIds).orEmpty()
        val serverReady = snapshot != null && allowedProducts.isNotEmpty()
        fun canBuy(productId: String): Boolean =
            !busy &&
                !active &&
                signedIn &&
                serverReady &&
                productId in products &&
                productId in allowedProducts
        binding.btnMonthly.isEnabled = canBuy(paymentClient.configuration.monthlyProductId)
        binding.btnYearly.isEnabled = canBuy(paymentClient.configuration.yearlyProductId)
        binding.btnRestore.isEnabled =
            !busy &&
            signedIn &&
            serverReady &&
            paymentClient.configuration.isComplete
        binding.btnRefresh.isEnabled = !busy && signedIn
        binding.btnSignIn.isVisible = !active && !signedIn

        when {
            !paymentClient.configuration.isComplete -> showBillingMessage(R.string.pro_pay_unconfigured)
            !signedIn -> showBillingMessage(R.string.pro_sign_in_required)
            entitlementState is EntitlementState.Checking ->
                showBillingMessage(R.string.pro_status_checking)
            entitlementState is EntitlementState.Unavailable ->
                showBillingMessage(R.string.pro_verification_unavailable)
            !serverReady -> showBillingMessage(R.string.pro_pay_unavailable)
            else -> binding.tvBillingMessage.isVisible = false
        }
    }

    private fun renderProductChoices(
        active: Boolean = EntitlementPolicy.isActivePro(entitlementState),
    ) {
        val allowedProducts = billingReadySnapshot()?.let(::allowedProductIds).orEmpty()
        val catalogResolved = resolvedCatalogFor == allowedProducts
        val loadedProducts = products.keys
        val monthlyVisible = shouldShowProProductCard(
            activePro = active,
            productId = paymentClient.configuration.monthlyProductId,
            allowedProductIds = allowedProducts,
            catalogResolved = catalogResolved,
            loadedProductIds = loadedProducts,
        )
        val yearlyVisible = shouldShowProProductCard(
            activePro = active,
            productId = paymentClient.configuration.yearlyProductId,
            allowedProductIds = allowedProducts,
            catalogResolved = catalogResolved,
            loadedProductIds = loadedProducts,
        )
        binding.cardMonthly.isVisible = monthlyVisible
        binding.cardYearly.isVisible = yearlyVisible
        binding.tvChoosePlan.isVisible = monthlyVisible || yearlyVisible
    }

    /** Only a fresh, canonical response may authorize a charge or restore. */
    private fun billingReadySnapshot(): EntitlementSnapshot? =
        // Read the repository synchronously at the action boundary. The UI's
        // collected copy can be one main-loop turn behind a refresh that just
        // moved Ready to Checking/Unavailable.
        EntitlementPolicy.billingSnapshotOrNull(entitlementRepository.state.value)
            ?.takeIf { paymentClient.configuration.isComplete }

    private fun allowedProductIds(snapshot: EntitlementSnapshot): Set<String> =
        snapshot.products.toSet().intersect(paymentClient.configuration.productIds.toSet())

    private fun invalidateProductCatalog() {
        products = emptyMap()
        resolvedCatalogFor = null
        binding.tvMonthlyDescription.text = ""
        binding.tvMonthlyDescription.isVisible = false
        binding.tvYearlyDescription.text = ""
        binding.tvYearlyDescription.isVisible = false
        binding.tvMonthlyPrice.setText(R.string.pro_price_loading)
        binding.tvYearlyPrice.setText(R.string.pro_price_loading)
    }

    private fun showProductFailure(message: Int, attemptedProductIds: Set<String>) {
        products = emptyMap()
        resolvedCatalogFor = attemptedProductIds
        binding.tvMonthlyPrice.setText(R.string.pro_price_unavailable)
        binding.tvYearlyPrice.setText(R.string.pro_price_unavailable)
        renderProductChoices()
        setBusy(false)
        showBillingMessage(message)
    }

    private fun openSubscriptionManagement() {
        try {
            startActivity(
                Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse("rustore://profile/subscriptions"),
                ),
            )
        } catch (_: ActivityNotFoundException) {
            try {
                startActivity(
                    Intent(
                        Intent.ACTION_VIEW,
                        Uri.parse(
                            "https://www.rustore.ru/help/users/purchases-and-returns/" +
                                "subscription-management",
                        ),
                    ),
                )
            } catch (_: ActivityNotFoundException) {
                Toast.makeText(this, R.string.pro_manage_unavailable, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showBillingMessage(message: Int) {
        binding.tvBillingMessage.setText(message)
        binding.tvBillingMessage.isVisible = true
    }

    private fun showMessage(message: Int) {
        showBillingMessage(message)
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    private fun setBusy(value: Boolean) {
        busy = value
        binding.progress.isVisible = value
        updateActions()
    }

    private fun payTheme(): PaySheetTheme = if (
        resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK ==
        Configuration.UI_MODE_NIGHT_YES
    ) {
        PaySheetTheme.DARK
    } else {
        PaySheetTheme.LIGHT
    }

    private fun onUi(block: () -> Unit) {
        runOnUiThread {
            if (!isFinishing && !isDestroyed) block()
        }
    }

    companion object {
        fun intent(context: Context): Intent = Intent(context, ProActivity::class.java)
    }
}

internal fun shouldShowProProductCard(
    activePro: Boolean,
    productId: String,
    allowedProductIds: Set<String>,
    catalogResolved: Boolean,
    loadedProductIds: Set<String>,
): Boolean =
    !activePro &&
        productId.isNotBlank() &&
        productId in allowedProductIds &&
        (!catalogResolved || productId in loadedProductIds)
