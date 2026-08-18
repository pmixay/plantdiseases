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
 * Pins the query invariant the treatment-plan duplication fix rests on.
 *
 * CareRepository.hasPlanForScan answers "was a plan ever derived from this
 * scan" by scanning [TreatmentPlanDao.getAllForPlant], so that query MUST keep
 * returning plans of every status. [TreatmentPlanDao.getActiveForPlant] cannot
 * answer it: `status = 'active'` stops matching the moment the user ticks the
 * final step, which is exactly when DetailExtraCardsController would otherwise
 * re-create the finished plan with its progress reset on the next visit.
 *
 * Narrowing getAllForPlant to active rows would silently reintroduce that bug
 * with no other test failing, which is why it is pinned here.
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

    private fun minimalScan(plantId: Long): ScanEntity = ScanEntity(
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

    private fun plan(plantId: Long, scanId: Long, status: String) = TreatmentPlanEntity(
        plantId = plantId,
        scanId = scanId,
        diseaseKey = "botrytis",
        diseaseLabelEn = "Botrytis",
        diseaseLabelRu = "Ботритис",
        status = status,
        completedAt = if (status == "active") null else System.currentTimeMillis(),
    )

    @Test
    fun getAllForPlant_stillReturnsThePlanAfterItIsCompleted() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val scanId = db.scanDao().insert(minimalScan(plantId))
        val planId = db.treatmentPlanDao().insert(plan(plantId, scanId, "active"))

        val active = db.treatmentPlanDao().getById(planId)!!
        db.treatmentPlanDao().update(
            active.copy(status = "completed", completedAt = System.currentTimeMillis()),
        )

        // The active-only lookup goes blind here. The all-status one must not:
        // it is what stops a finished plan being re-created from the same scan.
        assertThat(db.treatmentPlanDao().getAllForPlant(plantId).map { it.scanId })
            .containsExactly(scanId)
        assertThat(db.treatmentPlanDao().getActiveForPlant(plantId)).isNull()
    }

    @Test
    fun getAllForPlant_returnsAbandonedPlansToo() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val scanId = db.scanDao().insert(minimalScan(plantId))
        db.treatmentPlanDao().insert(plan(plantId, scanId, "abandoned"))

        assertThat(db.treatmentPlanDao().getAllForPlant(plantId)).hasSize(1)
    }

    @Test
    fun getAllForPlant_doesNotLeakAcrossScans() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val firstScanId = db.scanDao().insert(minimalScan(plantId))
        db.treatmentPlanDao().insert(plan(plantId, firstScanId, "completed"))
        val secondScanId = db.scanDao().insert(minimalScan(plantId))

        // A genuinely new scan must still be free to start its own treatment,
        // so no plan may report as belonging to it.
        assertThat(db.treatmentPlanDao().getAllForPlant(plantId).none { it.scanId == secondScanId })
            .isTrue()
    }
}
