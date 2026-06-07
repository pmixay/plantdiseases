package com.plantdiseases.app.testutil

import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity

/**
 * In-memory [ScanDao] so repository logic (saveScan mapping, getStats
 * rollups, parsing helpers) can be exercised in fast pure-JVM tests
 * without a device or Robolectric/Room. Auto-increments ids on insert
 * the same way Room does, and captures the most recently inserted
 * entity so tests can assert the AnalysisResponse → ScanEntity mapping.
 */
class FakeScanDao : ScanDao {

    val items = mutableListOf<ScanEntity>()
    private var nextId = 1L

    /** The exact entity object handed to [insert] last, with its id filled in. */
    var lastInserted: ScanEntity? = null
        private set

    override suspend fun getAllScans(): List<ScanEntity> =
        items.sortedByDescending { it.timestamp }

    override suspend fun getRecentScans(limit: Int): List<ScanEntity> =
        getAllScans().take(limit)

    override suspend fun getScanById(id: Long): ScanEntity? =
        items.firstOrNull { it.id == id }

    override suspend fun getScanByClientScanId(clientScanId: String): ScanEntity? =
        items.firstOrNull { it.clientScanId == clientScanId }

    override suspend fun getScansForPlant(plantId: Long): List<ScanEntity> =
        items.filter { it.plantId == plantId }.sortedByDescending { it.timestamp }

    override suspend fun insert(scan: ScanEntity): Long {
        val withId = scan.copy(id = nextId++)
        items.add(withId)
        lastInserted = withId
        return withId.id
    }

    override suspend fun assignScanToPlant(scanId: Long, plantId: Long?) {
        val idx = items.indexOfFirst { it.id == scanId }
        if (idx >= 0) items[idx] = items[idx].copy(plantId = plantId)
    }

    override suspend fun deleteById(id: Long) {
        items.removeAll { it.id == id }
    }

    override suspend fun deleteAll() {
        items.clear()
    }
}
