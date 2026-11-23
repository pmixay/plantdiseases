package com.plantdiseases.app.ui.common

import android.app.Application
import android.content.Context
import android.content.res.Configuration
import androidx.appcompat.view.ContextThemeWrapper
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Guards the shared push/pop motion every pushed screen (activity) uses. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class NavigationMotionResourcesTest {

    private val baseContext: Context
        get() = ApplicationProvider.getApplicationContext()

    @Test
    fun appThemeDefinesACompleteActivityPushPopPair() {
        assertThemeMotion(ContextThemeWrapper(baseContext, R.style.Theme_PlantDiseases))
    }

    @Test
    fun nightThemeRetainsTheActivityPushPopPair() {
        val nightConfiguration = Configuration(baseContext.resources.configuration).apply {
            uiMode =
                (uiMode and Configuration.UI_MODE_NIGHT_MASK.inv()) or
                Configuration.UI_MODE_NIGHT_YES
        }
        val nightContext = baseContext.createConfigurationContext(nightConfiguration)

        assertThemeMotion(ContextThemeWrapper(nightContext, R.style.Theme_PlantDiseases))
    }

    private fun assertThemeMotion(context: Context) {
        val themeAttributes = context.obtainStyledAttributes(
            intArrayOf(android.R.attr.windowAnimationStyle),
        )
        val animationStyle = themeAttributes.getResourceId(0, 0)
        themeAttributes.recycle()
        assertThat(animationStyle).isNotEqualTo(0)

        val animationAttributes = context.obtainStyledAttributes(
            animationStyle,
            ACTIVITY_ANIMATION_ATTRIBUTES,
        )
        ACTIVITY_ANIMATION_RESOURCES.forEachIndexed { index, expected ->
            val actual = animationAttributes.getResourceId(index, 0)
            assertThat(actual).isEqualTo(expected)
            context.resources.getAnimation(actual).close()
        }
        animationAttributes.recycle()
    }

    companion object {
        private val ACTIVITY_ANIMATION_ATTRIBUTES = intArrayOf(
            android.R.attr.activityOpenEnterAnimation,
            android.R.attr.activityOpenExitAnimation,
            android.R.attr.activityCloseEnterAnimation,
            android.R.attr.activityCloseExitAnimation,
        )
        private val ACTIVITY_ANIMATION_RESOURCES = intArrayOf(
            R.anim.navigation_forward_enter,
            R.anim.navigation_forward_exit,
            R.anim.navigation_back_enter,
            R.anim.navigation_back_exit,
        )
    }
}
