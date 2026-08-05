package com.plantdiseases.app.ui.result

import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.data.model.AnalysisResponse
import kotlinx.coroutines.runBlocking
import org.junit.Test
import org.junit.runner.RunWith

/**
 * End-to-end UI check that a stored diagnosis renders on the result
 * screen. Seeds a scan into the app's real Room DB, launches
 * [ResultActivity] for it and asserts the diagnosis name is shown.
 * Requires an emulator/device.
 */
@RunWith(AndroidJUnit4::class)
class ResultActivityTest {

    @Test
    fun rendersStoredDiagnosisName() {
        val app = ApplicationProvider.getApplicationContext<PlantDiseasesApp>()

        val scanId = runBlocking {
            app.scanRepository.saveScan(
                "/nonexistent-image.jpg",
                AnalysisResponse(
                    classKey = "leaf_spot",
                    diseaseName = "Leaf Spot",
                    diseaseNameRu = "Пятнистость листьев",
                    confidence = 0.77f,
                    isHealthy = false,
                    severity = "medium",
                    scanId = "ui-test-1",
                    allProbs = mapOf("leaf_spot" to 0.77f, "healthy" to 0.1f),
                ),
            )
        }

        val intent = Intent(app, ResultActivity::class.java)
            .putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)

        ActivityScenario.launch<ResultActivity>(intent).use {
            onView(withText("Leaf Spot")).check(matches(isDisplayed()))
        }

        runBlocking { app.scanRepository.deleteScan(scanId) }
    }

    @Test
    fun missingScanIdFinishesGracefully() {
        val app = ApplicationProvider.getApplicationContext<PlantDiseasesApp>()
        val intent = Intent(app, ResultActivity::class.java)
            .putExtra(ResultActivity.EXTRA_SCAN_ID, -1L)
        // Should not crash — onCreate finishes immediately when the id is absent.
        ActivityScenario.launch<ResultActivity>(intent).use { /* no-op */ }
    }
}
