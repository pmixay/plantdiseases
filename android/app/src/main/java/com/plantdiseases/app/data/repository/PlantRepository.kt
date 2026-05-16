package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.ChatMessageDao
import com.plantdiseases.app.data.local.ChatMessageEntity
import com.plantdiseases.app.data.local.PlantDao
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalDao
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.PlantReminderDao
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.data.local.PlantReminderEventDao
import com.plantdiseases.app.data.local.PlantReminderEventEntity
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class PlantRepository(
    private val plantDao: PlantDao,
    private val journalDao: PlantJournalDao,
    private val scanDao: ScanDao,
    private val reminderDao: PlantReminderDao,
    private val reminderEventDao: PlantReminderEventDao,
    private val chatMessageDao: ChatMessageDao,
) {
    suspend fun getPlants(): List<PlantSummary> = withContext(Dispatchers.IO) {
        plantDao.getAllPlants().map { plant ->
            val scans = scanDao.getScansForPlant(plant.id)
            val journal = journalDao.getEntriesForPlant(plant.id)
            val reminders = reminderDao.getForPlant(plant.id)
            PlantSummary(
                plant = plant,
                scanCount = scans.size,
                latestScan = scans.maxByOrNull { it.timestamp },
                lastJournalEntry = journal.firstOrNull(),
                nextAction = soonestReminderDueAt(reminders, journal),
            )
        }
    }

    /** Compute the soonest reminder due-time across all enabled reminders
     *  for a plant. ``dueAt = (mostRecent matching-type journal entry's
     *  timestamp OR plant_created_at) + interval_days * 86_400_000``.
     *  Returns ``null`` when no reminder is enabled. */
    private fun soonestReminderDueAt(
        reminders: List<PlantReminderEntity>,
        journal: List<PlantJournalEntity>,
    ): NextAction? {
        val active = reminders.filter { it.enabled }
        if (active.isEmpty()) return null
        return active.mapNotNull { reminder ->
            val journalType = reminderTypeToJournalType(reminder.type) ?: return@mapNotNull null
            val lastFired = journal.firstOrNull { it.type == journalType.value }?.timestamp
                ?: reminder.id // any constant — treat as "due now" via 0 fallback below
            val anchor = journal.firstOrNull { it.type == journalType.value }?.timestamp ?: 0L
            val dueAt = anchor + reminder.intervalDays.toLong() * 24L * 60L * 60L * 1000L
            NextAction(reminder.type, dueAt, lastFired)
        }.minByOrNull { it.dueAt }
    }

    private fun reminderTypeToJournalType(reminderType: String): JournalType? =
        when (reminderType) {
            "watering" -> JournalType.WATERING
            "fertilize" -> JournalType.FERTILISING
            // "check" reminders don't correspond to a logged journal type —
            // they're a re-shoot prompt. Treat as null so they don't gate
            // the next-action chip.
            else -> null
        }

    suspend fun getPlant(id: Long): PlantEntity? = withContext(Dispatchers.IO) {
        plantDao.getPlantById(id)
    }

    suspend fun savePlant(
        name: String,
        species: String?,
        existing: PlantEntity? = null,
        scientificName: String? = null,
        commonName: String? = null,
        room: String? = null,
    ): Long = withContext(Dispatchers.IO) {
        val cleanSpecies = species?.trim()?.takeIf { it.isNotEmpty() }
        val cleanScientific = scientificName?.trim()?.takeIf { it.isNotEmpty() }
        val cleanCommon = commonName?.trim()?.takeIf { it.isNotEmpty() }
        val cleanRoom = room?.trim()?.takeIf { it.isNotEmpty() }
        if (existing == null) {
            plantDao.insert(
                PlantEntity(
                    name = name.trim(),
                    species = cleanSpecies,
                    scientificName = cleanScientific,
                    commonName = cleanCommon,
                    room = cleanRoom,
                )
            )
        } else {
            plantDao.update(
                existing.copy(
                    name = name.trim(),
                    species = cleanSpecies,
                    // Preserve previously-stored canonical names when the
                    // caller didn't pass new values (edit dialog without a
                    // re-identification step).
                    scientificName = cleanScientific ?: existing.scientificName,
                    commonName = cleanCommon ?: existing.commonName,
                    room = cleanRoom ?: existing.room,
                )
            )
            existing.id
        }
    }

    suspend fun deletePlant(id: Long) = withContext(Dispatchers.IO) {
        plantDao.deleteById(id)
    }

    /** Update the user-picked cover photo on a plant. Pass `null` to
     *  clear it — the garden card then falls back to the latest-scan
     *  image (or the generic icon if there are no scans yet). */
    suspend fun updateCoverPhoto(id: Long, uri: String?) = withContext(Dispatchers.IO) {
        val existing = plantDao.getPlantById(id) ?: return@withContext
        plantDao.update(existing.copy(coverPhotoUri = uri))
    }

    /** Record the latest measured lux reading for a plant. Called
     *  when the Light Meter closes after a session launched with
     *  this plant's context. Stamps ``measuredLuxAt`` so the plant
     *  detail can render "measured 3 days ago". */
    suspend fun updateMeasuredLux(id: Long, lux: Int) = withContext(Dispatchers.IO) {
        if (lux <= 0) return@withContext
        val existing = plantDao.getPlantById(id) ?: return@withContext
        plantDao.update(existing.copy(
            measuredLux = lux,
            measuredLuxAt = System.currentTimeMillis(),
        ))
    }

    suspend fun assignScanToPlant(scanId: Long, plantId: Long?) = withContext(Dispatchers.IO) {
        scanDao.assignScanToPlant(scanId, plantId)
    }

    suspend fun getScansForPlant(plantId: Long): List<ScanEntity> = withContext(Dispatchers.IO) {
        scanDao.getScansForPlant(plantId)
    }

    /** Most-recent scan for a plant, or null if there have been none yet. */
    suspend fun getLatestScanForPlant(plantId: Long): ScanEntity? = withContext(Dispatchers.IO) {
        scanDao.getScansForPlant(plantId).firstOrNull()
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

    suspend fun addWatering(plantId: Long, text: String?) =
        addTypedEntry(plantId, JournalType.WATERING, text)

    suspend fun addFertilising(plantId: Long, text: String?) =
        addTypedEntry(plantId, JournalType.FERTILISING, text)

    suspend fun addRepotting(plantId: Long, text: String?) =
        addTypedEntry(plantId, JournalType.REPOTTING, text)

    suspend fun addPruning(plantId: Long, text: String?) =
        addTypedEntry(plantId, JournalType.PRUNING, text)

    suspend fun addMisting(plantId: Long, text: String?) =
        addTypedEntry(plantId, JournalType.MISTING, text)

    private suspend fun addTypedEntry(
        plantId: Long, type: JournalType, text: String?,
    ) = addJournalEntry(
        PlantJournalEntity(
            plantId = plantId,
            type = type.value,
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

    /** Upsert a reminder. Returns the row id so the caller can pass it
     *  to ``ReminderManager.schedule(reminderId = …)`` for event-log
     *  tracking. */
    suspend fun saveReminder(entity: PlantReminderEntity): Long =
        withContext(Dispatchers.IO) {
            val existing = reminderDao.getByPlantAndType(entity.plantId, entity.type)
            reminderDao.upsert(entity.copy(id = existing?.id ?: entity.id))
        }

    suspend fun deleteReminders(plantId: Long) = withContext(Dispatchers.IO) {
        reminderDao.deleteByPlant(plantId)
    }

    suspend fun getReminderById(id: Long): PlantReminderEntity? =
        withContext(Dispatchers.IO) { reminderDao.getById(id) }

    // ─── Reminder events ──────────────────────────────────────────────────────

    /** Record a reminder firing as a pending event. The worker calls
     *  this right before posting the notification. Returns the event
     *  id so the notification action receivers can resolve back to
     *  this row. */
    suspend fun recordReminderFire(reminder: PlantReminderEntity): Long =
        withContext(Dispatchers.IO) {
            reminderEventDao.insert(
                PlantReminderEventEntity(
                    reminderId = reminder.id,
                    plantId = reminder.plantId,
                    type = reminder.type,
                    firedAt = System.currentTimeMillis(),
                    action = "pending",
                )
            )
        }

    /** Resolve a previously-fired event with an action (done /
     *  snoozed / skipped). "done" additionally back-fills a matching
     *  journal entry (watering / fertilising) so the user sees their
     *  care record without an extra tap. */
    suspend fun resolveReminderEvent(eventId: Long, action: String) =
        withContext(Dispatchers.IO) {
            val event = reminderEventDao.getById(eventId) ?: return@withContext
            val now = System.currentTimeMillis()
            reminderEventDao.update(event.copy(action = action, doneAt = now))
            if (action == "done") {
                when (event.type) {
                    "watering" -> addWatering(event.plantId, text = null)
                    "fertilize" -> addFertilising(event.plantId, text = null)
                    // "check" doesn't have a matching journal type — it's
                    // a re-shoot prompt, not a logged care action.
                }
            }
        }

    suspend fun recentReminderEvents(plantId: Long, limit: Int = 50): List<PlantReminderEventEntity> =
        withContext(Dispatchers.IO) { reminderEventDao.recentForPlant(plantId, limit) }

    /** Epoch ms of the most-recent ``done`` event for this (plant, type)
     *  pair, or null if never done. Used to render "last watered N
     *  days ago" on the plant detail. */
    suspend fun lastReminderDoneAt(plantId: Long, type: String): Long? =
        withContext(Dispatchers.IO) { reminderEventDao.lastDoneAt(plantId, type) }

    /** Greg-style adaptive watering. Looks at the last ≥5
     *  done-resolution events for a (plant, type) reminder, computes
     *  the mean lateness vs the reminder's scheduled interval, and
     *  if the user is consistently early or late by more than a day
     *  emits a [WateringSuggestion] proposing a new interval. Never
     *  auto-edits the reminder — the UI surfaces this as a
     *  "shorten to N days?" chip the user accepts. */
    suspend fun adaptiveWateringSuggestion(
        plantId: Long,
        type: String = "watering",
        minSamples: Int = 5,
        deltaThresholdDays: Double = 1.0,
        maxDeltaDays: Int = 1,
    ): WateringSuggestion? = withContext(Dispatchers.IO) {
        val reminder = reminderDao.getByPlantAndType(plantId, type) ?: return@withContext null
        if (!reminder.enabled) return@withContext null
        val events = reminderEventDao.recentForPlant(plantId, limit = 50)
            .filter { it.type == type && it.action == "done" && it.doneAt != null }
            .sortedByDescending { it.firedAt }
            .take(minSamples * 2)
        if (events.size < minSamples) return@withContext null

        // Pair up adjacent "done" events by firedAt and treat the
        // gap between consecutive done_at timestamps as the user's
        // ACTUAL cadence. Then compare to the reminder's intended
        // interval; mean delta drives the suggestion.
        val sortedAsc = events.sortedBy { it.firedAt }
        val gaps = sortedAsc.zipWithNext { a, b ->
            val ax = a.doneAt ?: a.firedAt
            val bx = b.doneAt ?: b.firedAt
            (bx - ax).toDouble() / (24.0 * 60.0 * 60.0 * 1000.0)
        }
        if (gaps.isEmpty()) return@withContext null
        val meanGapDays = gaps.average()
        val deltaDays = meanGapDays - reminder.intervalDays.toDouble()
        if (kotlin.math.abs(deltaDays) < deltaThresholdDays) return@withContext null
        val proposedInterval = (reminder.intervalDays + deltaDays.toInt()
            .coerceIn(-maxDeltaDays, maxDeltaDays))
            .coerceIn(1, 30)
        if (proposedInterval == reminder.intervalDays) return@withContext null
        WateringSuggestion(
            type = type,
            currentIntervalDays = reminder.intervalDays,
            proposedIntervalDays = proposedInterval,
            meanGapDays = meanGapDays,
            samples = gaps.size + 1,
        )
    }

    /** Proposed interval change emitted by [adaptiveWateringSuggestion]. */
    data class WateringSuggestion(
        val type: String,
        val currentIntervalDays: Int,
        val proposedIntervalDays: Int,
        val meanGapDays: Double,
        val samples: Int,
    )

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
        val nextAction: NextAction? = null,
    )

    /** Soonest "do something" hint for a plant, computed off the most
     *  recently logged matching-type journal entry + reminder interval.
     *  ``dueAt`` is an absolute epoch-ms; the UI renders relative. */
    data class NextAction(
        val type: String,           // "watering" / "fertilize" / …
        val dueAt: Long,
        val lastFiredAt: Long,
    )

    enum class JournalType(val value: String) {
        NOTE("note"),
        PHOTO("photo"),
        WATERING("watering"),
        FERTILISING("fertilising"),
        REPOTTING("repotting"),
        PRUNING("pruning"),
        MISTING("misting");

        companion object {
            fun from(value: String) = entries.firstOrNull { it.value == value } ?: NOTE
        }
    }
}
