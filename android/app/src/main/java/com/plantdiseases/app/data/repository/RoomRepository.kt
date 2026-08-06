package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.PlantDao
import com.plantdiseases.app.data.local.RoomDao
import com.plantdiseases.app.data.local.RoomEntity
import com.plantdiseases.app.data.local.ScanDao
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Rooms as first-class objects (Garden refactor Phase 3) — see
 * RoomEntity's kdoc in Database.kt for why this replaced the free-text
 * `plants.room` column. This repository owns creation/rename/delete and
 * the room -> plants lookups the Garden tab and room-detail screen need;
 * PlantRepository stays the source of truth for plant rows themselves.
 */
class RoomRepository(
    private val roomDao: RoomDao,
    private val plantDao: PlantDao,
    private val scanDao: ScanDao,
) {
    data class RoomSummary(
        val room: RoomEntity,
        val plantCount: Int,
        /** Up to 3 thumbnail sources (cover photo, else latest scan image)
         *  for the room card's photo strip. */
        val thumbnails: List<String>,
    )

    sealed interface CreateRoomResult {
        data class Success(val roomId: Long) : CreateRoomResult
        object BlankName : CreateRoomResult
        object NameTaken : CreateRoomResult
    }

    sealed interface RenameRoomResult {
        object Success : RenameRoomResult
        object BlankName : RenameRoomResult
        object NameTaken : RenameRoomResult
    }

    suspend fun getRooms(): List<RoomSummary> = withContext(Dispatchers.IO) {
        val rooms = roomDao.getAll()
        val allPlants = plantDao.getAllPlants()
        rooms.map { room ->
            val plantsHere = allPlants.filter { it.roomId == room.id }
            val thumbs = plantsHere.mapNotNull { plant ->
                plant.coverPhotoUri ?: scanDao.getScansForPlant(plant.id).maxByOrNull { it.timestamp }?.imagePath
            }.take(THUMBNAIL_LIMIT)
            RoomSummary(room = room, plantCount = plantsHere.size, thumbnails = thumbs)
        }
    }

    /** Plants with no room. `roomId = null` in the caller's terms — this
     *  function name is deliberately distinct from [getPlantsInRoom] so a
     *  caller can't accidentally pass a literal `null` room id and mean
     *  something else. */
    suspend fun getUnassignedCount(): Int = withContext(Dispatchers.IO) {
        plantDao.getAllPlants().count { it.roomId == null }
    }

    suspend fun createRoom(name: String, icon: String?): CreateRoomResult = withContext(Dispatchers.IO) {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return@withContext CreateRoomResult.BlankName
        if (roomDao.findByName(trimmed) != null) return@withContext CreateRoomResult.NameTaken
        val id = roomDao.insert(RoomEntity(name = trimmed, icon = icon))
        CreateRoomResult.Success(id)
    }

    suspend fun renameRoom(id: Long, name: String): RenameRoomResult = withContext(Dispatchers.IO) {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return@withContext RenameRoomResult.BlankName
        val existing = roomDao.getById(id) ?: return@withContext RenameRoomResult.BlankName
        val clash = roomDao.findByName(trimmed)
        if (clash != null && clash.id != id) return@withContext RenameRoomResult.NameTaken
        roomDao.update(existing.copy(name = trimmed))
        RenameRoomResult.Success
    }

    /** Plants in the room survive — they fall into the unassigned bucket,
     *  never deleted alongside their room. */
    suspend fun deleteRoom(id: Long) = withContext(Dispatchers.IO) {
        plantDao.getAllPlants()
            .filter { it.roomId == id }
            .forEach { plantDao.update(it.copy(roomId = null)) }
        roomDao.deleteById(id)
    }

    suspend fun assignPlant(plantId: Long, roomId: Long?) = withContext(Dispatchers.IO) {
        val plant = plantDao.getPlantById(plantId) ?: return@withContext
        plantDao.update(plant.copy(roomId = roomId))
    }

    /** `roomId = null` returns the unassigned plants. */
    suspend fun getPlantsInRoom(roomId: Long?): List<com.plantdiseases.app.data.local.PlantEntity> =
        withContext(Dispatchers.IO) {
            plantDao.getAllPlants().filter { it.roomId == roomId }
        }

    suspend fun getRoom(id: Long): RoomEntity? = withContext(Dispatchers.IO) { roomDao.getById(id) }

    private companion object {
        const val THUMBNAIL_LIMIT = 3
    }
}
