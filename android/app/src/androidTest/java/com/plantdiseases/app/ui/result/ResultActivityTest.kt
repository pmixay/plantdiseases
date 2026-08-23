package com.plantdiseases.app.ui.result

import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso.onView
import androidx.test.espresso.assertion.ViewAssertions.doesNotExist
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.Visibility.GONE
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.espresso.matcher.ViewMatchers.withEffectiveVisibility
import androidx.test.espresso.matcher.ViewMatchers.withId
import androidx.test.espresso.matcher.ViewMatchers.withText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.di.appEntryPoint
import kotlinx.coroutines.runBlocking
import org.hamcrest.Matchers.containsString
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
        val scanRepository = appEntryPoint(app).scanRepository()

        val scanId = runBlocking {
            scanRepository.saveScan(
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

        runBlocking { scanRepository.deleteScan(scanId) }
    }

    @Test
    fun missingScanIdFinishesGracefully() {
        val app = ApplicationProvider.getApplicationContext<PlantDiseasesApp>()
        val intent = Intent(app, ResultActivity::class.java)
            .putExtra(ResultActivity.EXTRA_SCAN_ID, -1L)
        // Should not crash — onCreate finishes immediately when the id is absent.
        ActivityScenario.launch<ResultActivity>(intent).use { /* no-op */ }
    }

    @Test
    fun legacyOodWarning_rendersNeutralResultAndHidesDiseaseActions() {
        val app = ApplicationProvider.getApplicationContext<PlantDiseasesApp>()
        val scanRepository = appEntryPoint(app).scanRepository()
        val scanId = runBlocking {
            scanRepository.saveScan(
                "/nonexistent-ood-image.jpg",
                AnalysisResponse(
                    classKey = "leaf_spot",
                    diseaseName = "Leaf Spot",
                    diseaseNameRu = "Пятнистость листьев",
                    confidence = 0.83f,
                    description = "Raw disease description must not render",
                    treatment = listOf("Disease-specific treatment must not render"),
                    warnings = listOf("nap_ood_gated"),
                    scanId = "ui-test-legacy-ood",
                    allProbs = mapOf("leaf_spot" to 0.83f, "healthy" to 0.17f),
                ),
            )
        }
        val intent = Intent(app, ResultActivity::class.java)
            .putExtra(ResultActivity.EXTRA_SCAN_ID, scanId)

        try {
            ActivityScenario.launch<ResultActivity>(intent).use {
                onView(withText(R.string.inconclusive_diagnosis_name)).check(matches(isDisplayed()))
                onView(withText(R.string.inconclusive_result_body)).check(matches(isDisplayed()))
                onView(withText("Leaf Spot")).check(doesNotExist())
                onView(withText(containsString("83%"))).check(doesNotExist())
                onView(withText("Disease-specific treatment must not render")).check(doesNotExist())

                listOf(
                    R.id.tv_confidence,
                    R.id.progress_confidence,
                    R.id.tv_sticky_confidence,
                    R.id.heatmap_overlay,
                    R.id.btn_disease_appearance,
                    R.id.card_species,
                    R.id.card_alternatives,
                    R.id.card_similar_scans,
                    R.id.btn_ask_doctor,
                    R.id.btn_second_opinion,
                    R.id.btn_add_to_garden,
                    R.id.btn_share,
                ).forEach { viewId ->
                    onView(withId(viewId)).check(matches(withEffectiveVisibility(GONE)))
                }
            }
        } finally {
            runBlocking { scanRepository.deleteScan(scanId) }
        }
    }
}
