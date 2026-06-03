package com.plantdiseases.app.data.local

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.ScanRepository
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Integration test of the persistence layer driven through the real
 * Room DAO (in-memory) under Robolectric — saveScan → read-back, recent
 * vs all, delete, clear, and the stats rollup over genuine SQL.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ScanPersistenceTest {

    private lateinit var db: AppDatabase
    private lateinit var repo: ScanRepository

    @Before
    fun setup() {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(ctx, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        repo = ScanRepository(db.scanDao(), PlantApiClient("http://localhost/"))
    }

    @After
    fun teardown() {
        db.close()
    }

    @Test
    fun saveScan_persistsAndReadsBack() = runBlocking {
        val id = repo.saveScan(
            "/p.jpg",
            AnalysisResponse(
                classKey = "leaf_spot",
                diseaseName = "Leaf Spot",
                confidence = 0.8f,
                severity = "medium",
                scanId = "s1",
                isHealthy = false,
            ),
        )
        val row = repo.getScanById(id)!!
        assertThat(row.diseaseName).isEqualTo("Leaf Spot")
        assertThat(row.severity).isEqualTo("medium")
        assertThat(row.clientScanId).isEqualTo("s1")
        assertThat(row.displayedClassKey).isEqualTo("leaf_spot")
    }

    @Test
    fun recentAndAll_returnInsertedRows() = runBlocking {
        repo.saveScan("/a.jpg", AnalysisResponse(diseaseName = "A", scanId = "a"))
        repo.saveScan("/b.jpg", AnalysisResponse(diseaseName = "B", scanId = "b"))
        repo.saveScan("/c.jpg", AnalysisResponse(diseaseName = "C", scanId = "c"))
        assertThat(repo.getAllScans()).hasSize(3)
        assertThat(repo.getRecentScans(2)).hasSize(2)
    }

    @Test
    fun deleteScan_removesRow() = runBlocking {
        val id = repo.saveScan("/p.jpg", AnalysisResponse(diseaseName = "X", scanId = "x"))
        repo.deleteScan(id)
        assertThat(repo.getScanById(id)).isNull()
    }

    @Test
    fun clearAll_empties() = runBlocking {
        repo.saveScan("/p.jpg", AnalysisResponse(diseaseName = "X", scanId = "x"))
        repo.clearAll()
        assertThat(repo.getAllScans()).isEmpty()
    }

    @Test
    fun getStats_rollupOverRealDb() = runBlocking {
        repo.saveScan("/h.jpg", AnalysisResponse(diseaseName = "Healthy", isHealthy = true, scanId = "h"))
        repo.saveScan(
            "/d.jpg",
            AnalysisResponse(
                classKey = "leaf_spot",
                diseaseName = "Leaf Spot",
                isHealthy = false,
                allProbs = mapOf("leaf_spot" to 0.9f),
                scanId = "d",
            ),
        )
        val stats = repo.getStats()
        assertThat(stats.healthyCount).isEqualTo(1)
        assertThat(stats.diseasedCount).isEqualTo(1)
        assertThat(stats.totalScans).isEqualTo(2)
    }
}
