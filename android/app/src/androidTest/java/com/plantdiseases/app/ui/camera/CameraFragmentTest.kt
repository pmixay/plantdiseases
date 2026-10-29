package com.plantdiseases.app.ui.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withId
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import com.plantdiseases.app.R
import com.plantdiseases.app.testutil.FragmentTestActivity
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import java.util.regex.Pattern

/**
 * UI test for the camera capture screen. With the CAMERA runtime
 * permission ungranted (the default in an instrumented run), the
 * fragment must surface its in-app permission prompt rather than a black
 * preview or a crash. Requires an emulator/device.
 */
@RunWith(AndroidJUnit4::class)
class CameraFragmentTest {

    @Test
    fun showsPermissionPromptWhenCameraDenied() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        assertEquals(
            "Revoke CAMERA before running this denied-permission test",
            PackageManager.PERMISSION_DENIED,
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA),
        )
        val intent = Intent(context, FragmentTestActivity::class.java)
        ActivityScenario.launch<FragmentTestActivity>(intent).use { scenario ->
            scenario.onActivity { activity ->
                activity.supportFragmentManager.beginTransaction()
                    .replace(android.R.id.content, CameraFragment())
                    .commitNow()
            }
            denyCameraPermissionDialog()
            onView(withId(R.id.btn_grant_permission)).check(matches(isDisplayed()))
        }
    }

    private fun denyCameraPermissionDialog() {
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
        val deny = device.wait(
            Until.findObject(
                By.res(Pattern.compile(".*:id/permission_deny(_and_dont_ask_again)?_button")),
            ),
            5_000,
        )
        deny?.click()
        // An already-fixed denial has no system dialog; the in-app prompt
        // must still be displayed and is asserted by the caller.
    }
}
