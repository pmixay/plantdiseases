package com.plantdiseases.app.util

import android.os.Build
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.ErrorReport

/**
 * App-wide problem/error telemetry. Errors-only: nothing is ever sent on the
 * happy path — the app "sends more only when there's a problem". A short
 * in-memory breadcrumb trail travels with each report so a problem arrives with
 * the recent context that led to it. No images, no location, no PII.
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

    fun init(sender: (ErrorReport) -> Unit, consent: () -> Boolean) {
        this.sender = sender
        this.consent = consent
    }

    /** Record a lightweight breadcrumb (a screen open, a scan start, a retry).
     *  Kept in a bounded ring buffer; attached to the next error report. */
    @Synchronized
    fun crumb(action: String) {
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
        message: String? = null,
    ) {
        if (consent?.invoke() != true) return
        sender?.invoke(
            ErrorReport(
                stage = stage,
                kind = kind,
                fatal = fatal,
                httpStatus = httpStatus,
                family = family,
                appVersion = BuildConfig.VERSION_NAME,
                androidApi = Build.VERSION.SDK_INT,
                message = message,
                breadcrumbs = breadcrumbs(),
            ),
        )
    }
}
