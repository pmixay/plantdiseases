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
 * `plants.room` column. This repository owns the room rows themselves:
 * creation/rename/delete plus the per-room card summaries the Garden tab
 * renders. Which plants sit in a room is deliberately NOT looked up here —
 * both consumers (the Garden tab and RoomDetailActivity) need
 * [PlantRepository.PlantSummary] rows, so they filter PlantRepository's
 * output on `roomId` themselves; PlantRepository stays the source of truth
 * for plant rows.
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
         *  for the room card's photo strip. Always empty when the summary
         *  came from [getRoomsForPicker]. */
        val thumbnails: List<String>,
    )

    /** Outcome of [createRoom] / [renameRoom] — one shape for both, the
     *  way PlantDialogs already treats add and edit as one flow.
     *  [Success.roomId] is the created row for a create and the unchanged
     *  id for a rename. */
    sealed interface RoomNameResult {
        data class Success(val roomId: Long) : RoomNameResult
        data object BlankName : RoomNameResult
        data object NameTaken : RoomNameResult

        /** The room being renamed is gone (deleted from another screen). */
        data object NotFound : RoomNameResult
    }

    suspend fun getRooms(): List<RoomSummary> = withContext(Dispatchers.IO) {
        val rooms = roomDao.getAll()
        val allPlants = plantDao.getAllPlants()
        // One projection query for the whole garden instead of a full
        // 28-column scan-history read per plant just to reach image_path.
        val latestImages = scanDao.latestImageByPlant(allPlants.map { it.id })
            .associate { it.plantId to it.imagePath }
        rooms.map { room ->
            val plantsHere = allPlants.filter { it.roomId == room.id }
            // asSequence(): mapNotNull on a List runs to completion before
            // take() ever sees it, so a 40-plant room resolved 40 thumbnails
            // to keep 3.
            val thumbs = plantsHere.asSequence()
                .mapNotNull { plant -> plant.coverPhotoUri ?: latestImages[plant.id] }
                .take(THUMBNAIL_LIMIT)
                .toList()
            RoomSummary(room = room, plantCount = plantsHere.size, thumbnails = thumbs)
        }
    }

    /** Rooms without the thumbnail resolution [getRooms] does — for the
     *  room picker in the add/edit-plant dialog, which renders names only.
     *  Two queries flat, regardless of garden size. */
    suspend fun getRoomsForPicker(): List<RoomSummary> = withContext(Dispatchers.IO) {
        val allPlants = plantDao.getAllPlants()
        roomDao.getAll().map { room ->
            RoomSummary(
                room = room,
                plantCount = allPlants.count { it.roomId == room.id },
                thumbnails = emptyList(),
            )
        }
    }

    suspend fun createRoom(name: String, icon: String?): RoomNameResult = withContext(Dispatchers.IO) {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return@withContext RoomNameResult.BlankName
        if (findClash(trimmed) != null) return@withContext RoomNameResult.NameTaken
        RoomNameResult.Success(roomDao.insert(RoomEntity(name = trimmed, icon = icon)))
    }

    suspend fun renameRoom(id: Long, name: String): RoomNameResult = withContext(Dispatchers.IO) {
        val trimmed = name.trim()
        if (trimmed.isEmpty()) return@withContext RoomNameResult.BlankName
        val existing = roomDao.getById(id) ?: return@withContext RoomNameResult.NotFound
        val clash = findClash(trimmed)
        if (clash != null && clash.id != id) return@withContext RoomNameResult.NameTaken
        roomDao.update(existing.copy(name = trimmed))
        RoomNameResult.Success(id)
    }

    /** Name uniqueness is decided in Kotlin, never in SQL. SQLite's
     *  `COLLATE NOCASE` folds ASCII A–Z only, so the old `findByName`
     *  lookup happily let "Кухня" and "кухня" both exist behind the BINARY
     *  unique index — two identical-looking cards with the plants split
     *  between them. [String.equals] with `ignoreCase = true` is
     *  Unicode-aware and, unlike `lowercase()`, carries no Turkish
     *  dotless-I hazard. The table is a handful of rows. */
    private suspend fun findClash(trimmed: String): RoomEntity? =
        roomDao.getAll().firstOrNull { it.name.equals(trimmed, ignoreCase = true) }

    /** Plants in the room survive — they fall into the unassigned bucket,
     *  never deleted alongside their room. */
    suspend fun deleteRoom(id: Long) = withContext(Dispatchers.IO) {
        plantDao.getAllPlants()
            .filter { it.roomId == id }
            .forEach { plantDao.update(it.copy(roomId = null)) }
        roomDao.deleteById(id)
    }

    suspend fun getRoom(id: Long): RoomEntity? = withContext(Dispatchers.IO) { roomDao.getById(id) }

    private companion object {
        const val THUMBNAIL_LIMIT = 3
    }
}
