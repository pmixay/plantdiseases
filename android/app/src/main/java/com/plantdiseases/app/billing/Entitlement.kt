package com.plantdiseases.app.billing

import com.plantdiseases.app.data.account.EntitlementQuotaResponse
import java.time.Instant

/** All paid-feature decisions go through this enum instead of screen-local booleans. */
enum class ProFeature(val wireName: String) {
    LUNAR_FULL_CALENDAR("lunar_full_calendar"),
    LUNAR_GARDEN_AGGREGATE("lunar_garden_aggregate"),
}

data class EntitlementSnapshot(
    val plan: String,
    val status: String,
    val validUntil: String?,
    val autoRenewing: Boolean,
    /** True only when the account backend can verify new RuStore purchases. */
    val billingAvailable: Boolean,
    val billingExternalId: String?,
    val products: List<String>,
    /** Version of the server-side quota policy that produced [quotas]. */
    val quotaPolicyVersion: String?,
    val quotas: Map<String, EntitlementQuotaResponse>,
) {
    companion object {
        val FREE = EntitlementSnapshot(
            plan = "free",
            status = "none",
            validUntil = null,
            autoRenewing = false,
            billingAvailable = false,
            billingExternalId = null,
            products = emptyList(),
            quotaPolicyVersion = null,
            quotas = emptyMap(),
        )
    }
}

sealed interface EntitlementState {
    data object Free : EntitlementState

    data class Checking(val previous: EntitlementSnapshot? = null) : EntitlementState

    data class Ready(val entitlement: EntitlementSnapshot) : EntitlementState

    /** Keeps a still-valid in-memory server result usable during a brief outage. */
    data class Unavailable(val lastKnown: EntitlementSnapshot? = null) : EntitlementState
}

object EntitlementPolicy {
    fun snapshot(state: EntitlementState): EntitlementSnapshot? = when (state) {
        EntitlementState.Free -> EntitlementSnapshot.FREE
        is EntitlementState.Checking -> state.previous
        is EntitlementState.Ready -> state.entitlement
        is EntitlementState.Unavailable -> state.lastKnown
    }

    fun allows(
        state: EntitlementState,
        feature: ProFeature,
        now: Instant = Instant.now(),
    ): Boolean {
        val entitlement = snapshot(state) ?: return false
        // The enum is intentionally consumed even while the first backend
        // contract has plan-wide access. A later per-feature response can be
        // added here without changing every screen.
        feature.wireName
        return entitlement.isActivePro(now)
    }

    fun isActivePro(state: EntitlementState, now: Instant = Instant.now()): Boolean =
        snapshot(state)?.isActivePro(now) == true

    /**
     * Charging and restoration require stronger evidence than feature access:
     * only a fresh Ready response may be used, never a cached outage snapshot.
     */
    fun billingSnapshotOrNull(state: EntitlementState): EntitlementSnapshot? =
        (state as? EntitlementState.Ready)?.entitlement?.takeIf { entitlement ->
            entitlement.billingAvailable &&
                !entitlement.billingExternalId.isNullOrBlank() &&
                entitlement.products.isNotEmpty()
        }

    private fun EntitlementSnapshot.isActivePro(now: Instant): Boolean =
        plan.equals("pro", ignoreCase = true) &&
            status.lowercase() in ACTIVE_STATUSES &&
            isWithinValidity(now)

    private fun EntitlementSnapshot.isWithinValidity(now: Instant): Boolean {
        // The backend always returns an expiry for an active/trial/grace PRO
        // grant. A missing or malformed value is not proof of paid access.
        val rawExpiry = validUntil ?: return false
        val expiry = runCatching { Instant.parse(rawExpiry) }.getOrNull() ?: return false
        return expiry.isAfter(now)
    }

    private val ACTIVE_STATUSES = setOf("active", "trial", "grace")
}
