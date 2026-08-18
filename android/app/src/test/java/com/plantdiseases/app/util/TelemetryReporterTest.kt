package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.ErrorReport
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TelemetryReporterTest {
    private val reports = mutableListOf<ErrorReport>()
    private var consent = true

    @Before
    fun setup() {
        reports.clear()
        consent = true
        TelemetryReporter.init(reports::add) { consent }
    }

    @Test
    fun noConsentSendsNothing() {
        consent = false

        TelemetryReporter.report("analyze", "timeout", family = "gamma")

        assertThat(reports).isEmpty()
    }

    @Test
    fun reportContainsOnlyVersionedAllowlistedDimensions() {
        TelemetryReporter.crumb("opened:/private/user.jpg?token=secret")
        TelemetryReporter.crumb("scanner_opened")
        TelemetryReporter.report("analyze", "timeout", httpStatus = 504, family = "custom")

        val report = reports.single()
        assertThat(report.telemetryConsent).isTrue()
        assertThat(report.telemetryConsentVersion)
            .isEqualTo(PrivacyConsentPrefs.TELEMETRY_CONSENT_VERSION)
        assertThat(report.stage).isEqualTo("analyze")
        assertThat(report.kind).isEqualTo("timeout")
        assertThat(report.httpStatus).isEqualTo(504)
        assertThat(report.family).isEqualTo("unknown")
        assertThat(report.breadcrumbs).containsExactly("scanner_opened")
    }

    @Test
    fun unknownStageOrKindFailsClosedBeforeNetwork() {
        TelemetryReporter.report("path:/private", "timeout")
        TelemetryReporter.report("analyze", "java.lang.IllegalStateException")

        assertThat(reports).isEmpty()
    }
}
