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
import com.plantdiseases.app.data.local.RoomDao
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.local.TreatmentPlanDao
import com.plantdiseases.app.util.CareSchedule
import com.plantdiseases.app.util.PlantHealth
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class PlantRepository(
    private val plantDao: PlantDao,
    private val journalDao: PlantJournalDao,
    private val scanDao: ScanDao,
    private val reminderDao: PlantReminderDao,
    private val reminderEventDao: PlantReminderEventDao,
    private val chatMessageDao: ChatMessageDao,
    private val roomDao: RoomDao,
    private val treatmentPlanDao: TreatmentPlanDao,
) {
    /** Every plant with the garden list's decorations.
     *
     *  This used to issue four queries **per plant** — scans, journal,
     *  reminders, active plan — on the app's hottest screen, plus a fifth
     *  inside [getPlantHealth]. It is now four table-wide reads grouped in
     *  memory, so the cost is flat in garden size. `getAllScans`/`getAllEntries`
     *  keep the `ORDER BY timestamp DESC` of the per-plant queries they
     *  replace, and `groupBy` preserves encounter order, so the
     *  `firstOrNull()`/`maxByOrNull()` below still see exactly what they did.
     *
     *  The one query still made per plant is the adherence read inside
     *  [getPlantHealth], and only for plants with an active treatment plan —
     *  usually none. That one deliberately stays per-plant: it is
     *  `recentForPlant(limit = 50)`, and a batched `IN (…)` would apply that
     *  50-row cap across the whole garden rather than per plant, silently
     *  truncating the window for every plant but the busiest. */
    suspend fun getPlants(): List<PlantSummary> = withContext(Dispatchers.IO) {
        val roomNamesById = roomDao.getAll().associate { it.id to it.name }
        val nowMs = System.currentTimeMillis()
        val scansByPlant = scanDao.getAllScans().groupBy { it.plantId }
        val journalByPlant = journalDao.getAllEntries().groupBy { it.plantId }
        val remindersByPlant = reminderDao.getAll().groupBy { it.plantId }
        val plantIdsUnderTreatment = treatmentPlanDao.getAllActive().mapTo(mutableSetOf()) { it.plantId }
        plantDao.getAllPlants().map { plant ->
            val scans = scansByPlant[plant.id].orEmpty()
            val journal = journalByPlant[plant.id].orEmpty()
            val reminders = remindersByPlant[plant.id].orEmpty()
            PlantSummary(
                plant = plant,
                scanCount = scans.size,
                latestScan = scans.maxByOrNull { it.timestamp },
                lastJournalEntry = journal.firstOrNull(),
                nextAction = soonestReminderDueAt(reminders, journal, plant.createdAt),
                roomName = plant.roomId?.let { roomNamesById[it] },
                health = getPlantHealth(plant.id, scans, plant.id in plantIdsUnderTreatment, nowMs),
            )
        }
    }

    /** Name + thumbnail only, for the "add this scan to a plant" picker.
     *  [getPlants] costs 4–5 DAO round trips per plant (scans, journal,
     *  reminders, active plan, health/adherence) and a picker discards all
     *  of it, so this stays at two queries flat regardless of garden size:
     *  the plant rows plus one latest-photo projection over all of them. */
    suspend fun getPlantsForPicker(): List<PlantPickerItem> = withContext(Dispatchers.IO) {
        val plants = plantDao.getAllPlants()
        val latestImages = scanDao.latestImageByPlant(plants.map { it.id })
            .associate { it.plantId to it.imagePath }
        plants.map { plant ->
            PlantPickerItem(
                id = plant.id,
                name = plant.name,
                thumbnailSource = plant.coverPhotoUri ?: latestImages[plant.id],
            )
        }
    }

    /** Care adherence over the last [ADHERENCE_WINDOW_DAYS] days, from the
     *  already-logged reminder-firing history — cheaper than re-projecting
     *  CareQueue for every plant in the garden list, and only actually
     *  consulted when there's an active treatment plan (PlantHealth.evaluate
     *  ignores it otherwise). "Fired" reminders are the ones that were due;
     *  "done" ones were kept up with. */
    /** Public so the plant-detail health card (DetailExtraCardsController)
     *  can compute the same verdict for a single plant instead of
     *  duplicating the scoring + adherence logic a second time. */
    suspend fun getPlantHealth(
        plantId: Long,
        scans: List<ScanEntity>,
        hasActivePlan: Boolean,
        nowMs: Long = System.currentTimeMillis(),
    ): PlantHealth.Verdict {
        val adherence = if (hasActivePlan) {
            val cutoff = nowMs - ADHERENCE_WINDOW_DAYS * CareSchedule.DAY_MS
            val events = reminderEventDao.recentForPlant(plantId, limit = 50)
                .filter { it.firedAt >= cutoff }
            PlantHealth.Adherence(due = events.size, done = events.count { it.action == "done" })
        } else {
            PlantHealth.Adherence(due = 0, done = 0)
        }
        val scanInputs = scans.map { scan ->
            val isNotAPlant = scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
                scan.diseaseName.equals("Не растение", ignoreCase = true)
            PlantHealth.ScanInput(
                atMs = scan.timestamp,
                isHealthy = scan.isHealthy,
                confidence = scan.confidence,
                isNotAPlant = isNotAPlant,
            )
        }
        return PlantHealth.evaluate(scanInputs, hasActivePlan, adherence, nowMs)
    }

    /** Compute the soonest reminder due-time across all enabled reminders
     *  for a plant. ``dueAt = (mostRecent matching-type journal entry's
     *  timestamp OR plant_created_at) + interval_days * 86_400_000``.
     *  Returns ``null`` when no reminder is enabled.
     *
     *  The fallback anchor is the plant's creation time — anchoring at
     *  epoch 0 (the old behaviour) flagged every never-yet-logged
     *  reminder as "overdue" the moment it was enabled. */
    private fun soonestReminderDueAt(
        reminders: List<PlantReminderEntity>,
        journal: List<PlantJournalEntity>,
        plantCreatedAt: Long,
    ): NextAction? {
        val active = reminders.filter { it.enabled }
        if (active.isEmpty()) return null
        return active.mapNotNull { reminder ->
            val journalType = reminderTypeToJournalType(reminder.type) ?: return@mapNotNull null
            val anchor = journal.firstOrNull { it.type == journalType.value }?.timestamp
                ?: plantCreatedAt
            NextAction(reminder.type, CareSchedule.nextDueAt(anchor, reminder.intervalDays))
        }.minByOrNull { it.dueAt }
    }

    /** internal, not private: CareRepository.queue() reuses this exact
     *  mapping to compute the same anchor for the projected care queue —
     *  duplicating it would let the two drift apart. */
    internal fun reminderTypeToJournalType(reminderType: String): JournalType? =
        when (reminderType) {
            "watering" -> JournalType.WATERING
            "fertilize" -> JournalType.FERTILISING
            // Generic presets that map onto a logged care action anchor
            // on their matching journal type just like watering does.
            "mist" -> JournalType.MISTING
            "repot" -> JournalType.REPOTTING
            "prune" -> JournalType.PRUNING
            // "check" reminders (and the remaining generic presets) don't
            // correspond to a logged journal type — treat as null so they
            // don't gate the next-action chip.
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
        roomId: Long? = null,
    ): Long = withContext(Dispatchers.IO) {
        val cleanSpecies = species?.trim()?.takeIf { it.isNotEmpty() }
        val cleanScientific = scientificName?.trim()?.takeIf { it.isNotEmpty() }
        val cleanCommon = commonName?.trim()?.takeIf { it.isNotEmpty() }
        if (existing == null) {
            plantDao.insert(
                PlantEntity(
                    name = name.trim(),
                    species = cleanSpecies,
                    scientificName = cleanScientific,
                    commonName = cleanCommon,
                    roomId = roomId,
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
                    // Unlike the names above, roomId is always overwritten,
                    // never defaulted to the existing value — the room picker
                    // always sends the plant's current selection (including
                    // an explicit null for "unassign"), so falling back here
                    // would make unassigning a plant from its room impossible.
                    roomId = roomId,
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
        plantDao.update(
            existing.copy(
                measuredLux = lux,
                measuredLuxAt = System.currentTimeMillis(),
            )
        )
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
        plantId: Long,
        type: JournalType,
        text: String?,
    ) = addJournalEntry(
        PlantJournalEntity(
            plantId = plantId,
            type = type.value,
            text = text?.trim()?.takeIf { it.isNotEmpty() },
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
     *  tracking. The read-then-write merge runs inside a single Room
     *  ``@Transaction`` so two simultaneous calls on the same
     *  (plant_id, type) can't both insert. */
    suspend fun saveReminder(entity: PlantReminderEntity): Long =
        withContext(Dispatchers.IO) { reminderDao.upsertByPlantAndType(entity) }

    /** Upsert a reminder ROW by primary key (no merge-by-type). Used by
     *  the generic-reminder editor, where several reminders may share a
     *  type (e.g. two "custom" rows on one plant). */
    suspend fun saveReminderRow(entity: PlantReminderEntity): Long =
        withContext(Dispatchers.IO) {
            val rowId = reminderDao.upsert(entity)
            if (entity.id != 0L) entity.id else rowId
        }

    suspend fun deleteReminder(id: Long) =
        withContext(Dispatchers.IO) { reminderDao.deleteById(id) }

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
        val proposedInterval = (
            reminder.intervalDays + deltaDays.toInt()
                .coerceIn(-maxDeltaDays, maxDeltaDays)
            )
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
        val roomName: String? = null,
        val health: PlantHealth.Verdict = PlantHealth.Verdict(
            status = PlantHealth.Status.UNKNOWN,
            score = null,
            weekDelta = null,
            series = emptyList(),
            scanCount = 0,
        ),
    )

    /** One row of [getPlantsForPicker]: everything a name + photo list needs
     *  and nothing else. [thumbnailSource] is the user-picked cover photo,
     *  else the newest scan's image, else null. */
    data class PlantPickerItem(
        val id: Long,
        val name: String,
        val thumbnailSource: String?,
    )

    /** Soonest "do something" hint for a plant, computed off the most
     *  recently logged matching-type journal entry + reminder interval.
     *  ``dueAt`` is an absolute epoch-ms; the UI renders relative. */
    data class NextAction(
        val type: String, // "watering" / "fertilize" / …
        val dueAt: Long,
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

    private companion object {
        const val ADHERENCE_WINDOW_DAYS = 30L
    }
}
