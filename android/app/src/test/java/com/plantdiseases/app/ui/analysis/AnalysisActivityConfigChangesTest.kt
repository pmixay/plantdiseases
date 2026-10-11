package com.plantdiseases.app.ui.analysis

import android.content.ComponentName
import android.content.Context
import android.content.pm.ActivityInfo
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * AnalysisActivity runs one paid server analysis and then the Room write of
 * the scan the server issued an id for. A recreate cancels that work and
 * onCreate starts a second analysis, so the activity must handle every
 * configuration change a user can trigger mid-scan instead of being recreated.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AnalysisActivityConfigChangesTest {

    @Test
    fun handlesEveryUserTriggerableConfigChangeWithoutRecreating() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val info = context.packageManager.getActivityInfo(
            ComponentName(context, AnalysisActivity::class.java),
            0,
        )
        val required = mapOf(
            "uiMode (night mode)" to ActivityInfo.CONFIG_UI_MODE,
            "locale" to ActivityInfo.CONFIG_LOCALE,
            "layoutDirection" to ActivityInfo.CONFIG_LAYOUT_DIRECTION,
            "fontScale" to ActivityInfo.CONFIG_FONT_SCALE,
            "density" to ActivityInfo.CONFIG_DENSITY,
            "orientation (large screens ignore the portrait lock)" to ActivityInfo.CONFIG_ORIENTATION,
            "screenSize (multi-window resize)" to ActivityInfo.CONFIG_SCREEN_SIZE,
            "smallestScreenSize" to ActivityInfo.CONFIG_SMALLEST_SCREEN_SIZE,
            "screenLayout" to ActivityInfo.CONFIG_SCREEN_LAYOUT,
        )
        val missing = required.filterValues { flag -> info.configChanges and flag == 0 }.keys
        assertThat(missing).isEmpty()
    }
}
