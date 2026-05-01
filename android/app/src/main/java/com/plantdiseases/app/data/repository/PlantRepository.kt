package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.ChatMessageDao
import com.plantdiseases.app.data.local.ChatMessageEntity
import com.plantdiseases.app.data.local.PlantDao
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalDao
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.PlantReminderDao
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class PlantRepository(
    private val plantDao: PlantDao,
    private val journalDao: PlantJournalDao,
    private val scanDao: ScanDao,
    private val reminderDao: PlantReminderDao,
    private val chatMessageDao: ChatMessageDao,
) {
    suspend fun getPlants(): List<PlantSummary> = withContext(Dispatchers.IO) {
        plantDao.getAllPlants().map { plant ->
            val scans = scanDao.getScansForPlant(plant.id)
            PlantSummary(
                plant = plant,
                scanCount = scans.size,
                latestScan = scans.maxByOrNull { it.timestamp },
                lastJournalEntry = journalDao.getEntriesForPlant(plant.id).firstOrNull(),
            )
        }
    }

    suspend fun getPlant(id: Long): PlantEntity? = withContext(Dispatchers.IO) {
        plantDao.getPlantById(id)
    }

    suspend fun savePlant(
        name: String,
        species: String?,
        nickname: String?,
        existing: PlantEntity? = null,
        scientificName: String? = null,
        commonName: String? = null,
    ): Long = withContext(Dispatchers.IO) {
        val cleanSpecies = species?.trim()?.takeIf { it.isNotEmpty() }
        val cleanNickname = nickname?.trim()?.takeIf { it.isNotEmpty() }
        val cleanScientific = scientificName?.trim()?.takeIf { it.isNotEmpty() }
        val cleanCommon = commonName?.trim()?.takeIf { it.isNotEmpty() }
        if (existing == null) {
            plantDao.insert(
                PlantEntity(
                    name = name.trim(),
                    species = cleanSpecies,
                    nickname = cleanNickname,
                    scientificName = cleanScientific,
                    commonName = cleanCommon,
                )
            )
        } else {
            plantDao.update(
                existing.copy(
                    name = name.trim(),
                    species = cleanSpecies,
                    nickname = cleanNickname,
                    // Preserve previously-stored canonical names when the
                    // caller didn't pass new values (edit dialog without a
                    // re-identification step).
                    scientificName = cleanScientific ?: existing.scientificName,
                    commonName = cleanCommon ?: existing.commonName,
                )
            )
            existing.id
        }
    }

    suspend fun deletePlant(id: Long) = withContext(Dispatchers.IO) {
        plantDao.deleteById(id)
    }

    suspend fun assignScanToPlant(scanId: Long, plantId: Long?) = withContext(Dispatchers.IO) {
        scanDao.assignScanToPlant(scanId, plantId)
    }

    suspend fun getScansForPlant(plantId: Long): List<ScanEntity> = withContext(Dispatchers.IO) {
        scanDao.getScansForPlant(plantId)
    }

    suspend fun getJournalEntries(plantId: Long): List<PlantJournalEntity> =
        withContext(Dispatchers.IO) {
            journalDao.getEntriesForPlant(plantId)
        }

    suspend fun addNote(plantId: Long, text: String) = addJournalEntry(
        PlantJournalEntity(
            plantId = plantId,
            type = JournalType.NOTE.value,
            text = text.trim(),
        )
    )

    suspend fun addWatering(plantId: Long, text: String?) = addJournalEntry(
        PlantJournalEntity(
            plantId = plantId,
            type = JournalType.WATERING.value,
            text = text?.trim()?.takeIf { it.isNotEmpty() },
        )
    )

    suspend fun addPhoto(plantId: Long, photoUri: String, text: String?) = addJournalEntry(
        PlantJournalEntity(
            plantId = plantId,
            type = JournalType.PHOTO.value,
            text = text?.trim()?.takeIf { it.isNotEmpty() },
            photoUri = photoUri,
        )
    )

    private suspend fun addJournalEntry(entry: PlantJournalEntity): Long =
        withContext(Dispatchers.IO) {
            journalDao.insert(entry)
        }

    suspend fun deleteJournalEntry(id: Long) = withContext(Dispatchers.IO) {
        journalDao.deleteById(id)
    }

    suspend fun updateJournalEntryText(id: Long, text: String?) = withContext(Dispatchers.IO) {
        val existing = journalDao.getById(id) ?: return@withContext
        val cleaned = text?.trim()?.takeIf { it.isNotEmpty() }
        journalDao.update(existing.copy(text = cleaned))
    }

    // ─── Reminders ────────────────────────────────────────────────────────────

    suspend fun getReminders(plantId: Long): List<PlantReminderEntity> =
        withContext(Dispatchers.IO) { reminderDao.getForPlant(plantId) }

    suspend fun saveReminder(entity: PlantReminderEntity) = withContext(Dispatchers.IO) {
        val existing = reminderDao.getByPlantAndType(entity.plantId, entity.type)
        reminderDao.upsert(entity.copy(id = existing?.id ?: entity.id))
    }

    suspend fun deleteReminders(plantId: Long) = withContext(Dispatchers.IO) {
        reminderDao.deleteByPlant(plantId)
    }

    // ─── Chat Messages ────────────────────────────────────────────────────────

    suspend fun getChatMessages(plantId: Long): List<ChatMessageEntity> =
        withContext(Dispatchers.IO) { chatMessageDao.getForPlant(plantId) }

    suspend fun saveChatMessage(entity: ChatMessageEntity): Long =
        withContext(Dispatchers.IO) { chatMessageDao.insert(entity) }

    suspend fun clearChat(plantId: Long) = withContext(Dispatchers.IO) {
        chatMessageDao.deleteForPlant(plantId)
    }

    data class PlantSummary(
        val plant: PlantEntity,
        val scanCount: Int,
        val latestScan: ScanEntity?,
        val lastJournalEntry: PlantJournalEntity?,
    )

    enum class JournalType(val value: String) {
        NOTE("note"),
        PHOTO("photo"),
        WATERING("watering");

        companion object {
            fun from(value: String) = entries.firstOrNull { it.value == value } ?: NOTE
        }
    }
}
