package com.plantdiseases.app.util

import android.os.Build
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.ErrorReport

/**
 * App-wide problem/error telemetry. Errors-only: nothing is ever sent on the
 * happy path. A short allowlisted in-memory breadcrumb trail travels with each
 * report. No exception text, paths, URLs, images, location, or identifiers.
 *
 * Consent-gated and strictly best-effort. Wiring lives in [PlantDiseasesApp]:
 * it registers the sender (fire-and-forget POST on the app scope) and the
 * consent check, installs the uncaught-exception handler, and flushes any crash
 * persisted from the previous run.
 */
object TelemetryReporter {
    private const val MAX_CRUMBS = 20
    private val crumbs = ArrayDeque<String>()
    private var sender: ((ErrorReport) -> Unit)? = null
    private var consent: (() -> Boolean)? = null

    @Synchronized
    fun init(sender: (ErrorReport) -> Unit, consent: () -> Boolean) {
        this.sender = sender
        this.consent = consent
        crumbs.clear()
    }

    /** Record a lightweight breadcrumb (a screen open, a scan start, a retry).
     *  Kept in a bounded ring buffer; attached to the next error report. */
    @Synchronized
    fun crumb(action: String) {
        if (action !in allowedBreadcrumbs) return
        if (crumbs.size >= MAX_CRUMBS) crumbs.removeFirst()
        crumbs.addLast(action)
    }

    @Synchronized
    fun breadcrumbs(): List<String> = crumbs.toList()

    /** Report a handled problem. No-op without data consent. */
    fun report(
        stage: String,
        kind: String,
        fatal: Boolean = false,
        httpStatus: Int? = null,
        family: String? = null,
    ) {
        if (consent?.invoke() != true) return
        if (stage !in allowedStages || kind !in allowedKinds) return
        val safeFamily = family?.takeIf { it in allowedFamilies } ?: family?.let { "unknown" }
        sender?.invoke(
            ErrorReport(
                telemetryConsent = true,
                telemetryConsentVersion = PrivacyConsentPrefs.TELEMETRY_CONSENT_VERSION,
                stage = stage,
                kind = kind,
                fatal = fatal,
                httpStatus = httpStatus,
                family = safeFamily,
                appVersion = BuildConfig.VERSION_NAME,
                androidApi = Build.VERSION.SDK_INT,
                breadcrumbs = breadcrumbs(),
            ),
        )
    }

    private val allowedStages = setOf(
        "image_prepare",
        "analyze",
        "aqua_inference",
        "crash",
        "community",
        "authentication",
        "startup",
    )
    private val allowedKinds = setOf(
        "decode_failed",
        "out_of_memory",
        "internal_error",
        "timeout",
        "no_network",
        "rate_limited",
        "server_starting",
        "unsupported_image",
        "uncaught_exception",
        "http_error",
        "model_unavailable",
        "invalid_envelope",
    )
    private val allowedFamilies = setOf("aqua", "gamma", "frontier", "unknown")
    private val allowedBreadcrumbs = setOf(
        "scanner_opened",
        "analyze_started_aqua",
        "analyze_started_gamma",
        "analyze_started_frontier",
        "retry_started",
        "result_opened",
        "community_opened",
    )
}
