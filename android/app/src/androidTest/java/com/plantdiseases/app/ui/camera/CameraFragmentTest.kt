package com.plantdiseases.app.ui.camera

import androidx.fragment.app.testing.launchFragmentInContainer
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withId
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith

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
        launchFragmentInContainer<CameraFragment>(themeResId = R.style.Theme_PlantDiseases)
        onView(withId(R.id.btn_grant_permission)).check(matches(isDisplayed()))
    }
}
