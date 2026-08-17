package com.plantdiseases.app.data.local

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Pins the fix for the treatment-plan duplication bug: a completed plan
 * must still count as "already exists" for its scan+disease, or
 * DetailExtraCardsController.maybeStartTreatmentPlan spins up a duplicate
 * every time the plant screen reloads. [TreatmentPlanDao.getActiveForPlant]
 * alone can't answer this — it stops matching the moment status leaves
 * "active" — which is exactly the bug this pins.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TreatmentPlanDaoTest {

    private lateinit var db: AppDatabase

    @Before
    fun setup() {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(ctx, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
    }

    @After
    fun teardown() {
        db.close()
    }

    private fun minimalScan(plantId: Long? = null): ScanEntity = ScanEntity(
        imagePath = "file://scan.jpg",
        diseaseName = "Botrytis",
        diseaseNameRu = "Ботритис",
        confidence = 0.9f,
        description = "",
        descriptionRu = "",
        treatment = "[]",
        treatmentRu = "[]",
        prevention = "[]",
        preventionRu = "[]",
        isHealthy = false,
        plantId = plantId,
    )

    @Test
    fun existsForScanAndDisease_survivesCompletion() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val scanId = db.scanDao().insert(minimalScan(plantId))
        val planId = db.treatmentPlanDao().insert(
            TreatmentPlanEntity(
                plantId = plantId,
                scanId = scanId,
                diseaseKey = "botrytis",
                diseaseLabelEn = "Botrytis",
                diseaseLabelRu = "Ботритис",
                status = "active",
            ),
        )

        assertThat(db.treatmentPlanDao().existsForScanAndDisease(plantId, scanId, "botrytis")).isTrue()

        // The user finishes their treatment — this is the exact moment the
        // old activePlan()-based guard started reading as "no plan yet".
        val active = db.treatmentPlanDao().getById(planId)!!
        db.treatmentPlanDao().update(active.copy(status = "completed", completedAt = System.currentTimeMillis()))

        assertThat(db.treatmentPlanDao().getActiveForPlant(plantId)).isNull()
        assertThat(db.treatmentPlanDao().existsForScanAndDisease(plantId, scanId, "botrytis")).isTrue()
    }

    @Test
    fun existsForScanAndDisease_falseForADifferentScan() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val firstScanId = db.scanDao().insert(minimalScan(plantId))
        db.treatmentPlanDao().insert(
            TreatmentPlanEntity(
                plantId = plantId,
                scanId = firstScanId,
                diseaseKey = "botrytis",
                diseaseLabelEn = "Botrytis",
                diseaseLabelRu = "Ботритис",
                status = "completed",
                completedAt = System.currentTimeMillis(),
            ),
        )
        val secondScanId = db.scanDao().insert(minimalScan(plantId))

        // A genuinely new scan of the same disease must still be free to
        // start its own course of treatment.
        assertThat(
            db.treatmentPlanDao().existsForScanAndDisease(plantId, secondScanId, "botrytis"),
        ).isFalse()
    }
}
