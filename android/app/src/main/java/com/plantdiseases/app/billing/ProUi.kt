package com.plantdiseases.app.billing

/**
 * PRO is not offered yet. While [ENABLED] is false no screen, badge, dialog or
 * text mentions it and nothing in the app is locked behind it. The billing and
 * entitlement code underneath stays in place for a later release; flipping
 * this switch is what brings its entry points back.
 */
object ProUi {
    const val ENABLED = false

    /** The gate as screens apply it: every feature is open until PRO is offered. */
    fun allows(
        state: EntitlementState,
        feature: ProFeature,
        offered: Boolean = ENABLED,
    ): Boolean = !offered || EntitlementPolicy.allows(state, feature)
}
