package com.plantdiseases.app.ui.common

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
import org.xmlpull.v1.XmlPullParser

/** Guards the shared push/pop motion used by activities and the Guide route. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
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

    @Test
    fun guideGraphActionUsesTheSharedPushPopPair() {
        val parser = baseContext.resources.getXml(R.navigation.nav_graph)
        var found = false
        while (parser.eventType != XmlPullParser.END_DOCUMENT) {
            if (
                parser.eventType == XmlPullParser.START_TAG &&
                parser.name == "action" &&
                parser.getAttributeResourceValue(ANDROID_NAMESPACE, "id", 0) ==
                R.id.action_shellFragment_to_guideFragment
            ) {
                found = true
                assertThat(parser.getAttributeResourceValue(APP_NAMESPACE, "enterAnim", 0))
                    .isEqualTo(R.anim.navigation_forward_enter)
                assertThat(parser.getAttributeResourceValue(APP_NAMESPACE, "exitAnim", 0))
                    .isEqualTo(R.anim.navigation_forward_exit)
                assertThat(parser.getAttributeResourceValue(APP_NAMESPACE, "popEnterAnim", 0))
                    .isEqualTo(R.anim.navigation_back_enter)
                assertThat(parser.getAttributeResourceValue(APP_NAMESPACE, "popExitAnim", 0))
                    .isEqualTo(R.anim.navigation_back_exit)
                break
            }
            parser.next()
        }
        parser.close()

        assertThat(found).isTrue()
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
        private const val ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"
        private const val APP_NAMESPACE = "http://schemas.android.com/apk/res-auto"

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
