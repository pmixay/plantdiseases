package com.plantdiseases.app.data.repository

import androidx.room.withTransaction
import com.plantdiseases.app.data.local.AppDatabase
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
import com.plantdiseases.app.data.local.TreatmentPlanEntity
import com.plantdiseases.app.data.local.TreatmentStepDao
import com.plantdiseases.app.util.CareQueue
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
    private val treatmentStepDao: TreatmentStepDao,
    private val database: AppDatabase,
) {
    enum class CanonicalSpeciesUpdate { PRESERVE, REPLACE }

    /** Every plant with the garden list's decorations.
     *
     *  This used to issue four queries **per plant** — scans, journal,
     *  reminders, active plan — on the app's hottest screen, plus a fifth
     *  inside [getPlantHealth]. It is now four table-wide reads grouped in
     *  memory, so the cost is flat in garden size. The table-wide reads
     *  keep the `ORDER BY timestamp DESC` of the per-plant queries they
     *  replace, and `groupBy` preserves encounter order, so the
     *  `firstOrNull()`/`maxByOrNull()` below still see exactly what they did.
     *
     *  The queries still made per plant are the adherence reads inside
     *  [getPlantHealth], and only for plants with an active treatment plan —
     *  usually none. The reminder-event read deliberately stays per-plant: it is
     *  `recentForPlant(limit = 50)`, and a batched `IN (…)` would apply that
     *  50-row cap across the whole garden rather than per plant, silently
     *  truncating the window for every plant but the busiest. */
    suspend fun getPlants(): List<PlantSummary> = withContext(Dispatchers.IO) {
        val roomNamesById = roomDao.getAll().associate { it.id to it.name }
        val nowMs = System.currentTimeMillis()
        val scansByPlant = scanDao.getAllAssignedScans()
            .filterNot(PersistedScanAbstentionPolicy::isAbstained)
            .groupBy { it.plantId }
        val journalByPlant = journalDao.getAllEntries().groupBy { it.plantId }
        val remindersByPlant = reminderDao.getAll().groupBy { it.plantId }
        val activePlansByPlant = treatmentPlanDao.getAllActive().associateBy { it.plantId }
        plantDao.getAllPlants().map { plant ->
            val scans = scansByPlant[plant.id].orEmpty()
            val journal = journalByPlant[plant.id].orEmpty()
            val reminders = remindersByPlant[plant.id].orEmpty()
            PlantSummary(
                plant = plant,
                scanCount = scans.size,
                latestScan = scans.maxByOrNull { it.timestamp },
                nextAction = soonestReminderDueAt(reminders, journal),
                roomName = plant.roomId?.let { roomNamesById[it] },
                health = getPlantHealth(plant.id, scans, activePlansByPlant[plant.id], nowMs),
            )
        }
    }

    /** Name + thumbnail only, for the "add this scan to a plant" picker.
     *  [getPlants] reads journal/reminder/health data a picker discards. This
     *  still uses two flat queries, but reads assigned scan rows so legacy
     *  abstentions cannot become a plant's fallback cover photo. */
    suspend fun getPlantsForPicker(): List<PlantPickerItem> = withContext(Dispatchers.IO) {
        val plants = plantDao.getAllPlants()
        val latestImages = scanDao.getAllAssignedScans()
            .asSequence()
            .filterNot(PersistedScanAbstentionPolicy::isAbstained)
            .groupBy { it.plantId }
            .mapNotNull { (plantId, scans) ->
                plantId?.let { it to scans.maxByOrNull { scan -> scan.timestamp }?.imagePath }
            }
            .toMap()
        plants.map { plant ->
            PlantPickerItem(
                id = plant.id,
                name = plant.name,
                thumbnailSource = plant.coverPhotoUri ?: latestImages[plant.id],
            )
        }
    }

    /** The [PlantHealth.Verdict] for one plant — the garden list and the
     *  plant-detail screen (header + health card) both read it from here, so
     *  the scoring and adherence logic exist once.
     *
     *  Abstained scans (blur, OOD, not-a-plant) are dropped before scoring.
     *  Adherence is only read when there's an active treatment plan
     *  (PlantHealth.evaluate ignores it otherwise): the plan's due checklist
     *  steps plus reminder firings since the plan started (at most the last
     *  [ADHERENCE_WINDOW_DAYS] days), from the already-logged history —
     *  cheaper than re-projecting CareQueue for every plant in the garden
     *  list. "Fired" reminders are the ones that were due; "done" ones were
     *  kept up with. */
    suspend fun getPlantHealth(
        plantId: Long,
        scans: List<ScanEntity>,
        activePlan: TreatmentPlanEntity?,
        nowMs: Long = System.currentTimeMillis(),
    ): PlantHealth.Verdict {
        val adherence = if (activePlan != null) {
            // Only reminders that fell due while this plan has been running:
            // care logged before the diagnosis says nothing about keeping up
            // with the treatment, and counting it let a brand-new plan look
            // fully kept up before a single treatment step was done.
            val cutoff = maxOf(nowMs - ADHERENCE_WINDOW_DAYS * CareSchedule.DAY_MS, activePlan.startedAt)
            val events = reminderEventDao.recentForPlant(plantId, limit = 50)
                .filter { it.firedAt >= cutoff }
            // The scan-derived treatment checklist is the primary active-plan
            // adherence signal. Reminder events remain additive so scheduled
            // watering/fertilising done during treatment also counts.
            val dueSteps = treatmentStepDao.getForPlan(activePlan.id)
                .filter { it.dueAt <= nowMs && it.status != "skipped" }
            PlantHealth.Adherence(
                due = events.size + dueSteps.size,
                done = events.count { it.action == "done" } + dueSteps.count { it.status == "done" },
            )
        } else {
            PlantHealth.Adherence(due = 0, done = 0)
        }
        val scanInputs = scans
            .filterNot(PersistedScanAbstentionPolicy::isAbstained)
            .map { scan ->
                val displayedClassKey = scan.displayedClassKey?.takeIf { it.isNotBlank() }
                val isNotAPlant = if (displayedClassKey != null) {
                    displayedClassKey == "not_a_plant"
                } else {
                    scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
                        scan.diseaseNameRu.equals("Не растение", ignoreCase = true)
                }
                PlantHealth.ScanInput(
                    atMs = scan.timestamp,
                    isHealthy = scan.isHealthy,
                    confidence = scan.confidence,
                    isNotAPlant = isNotAPlant,
                )
            }
        return PlantHealth.evaluate(scanInputs, activePlan != null, adherence, nowMs)
    }

    /** Compute the soonest reminder due-time across all enabled reminders
     *  for a plant. ``dueAt = max(mostRecent matching care log,
     *  reminder_created_at) + interval_days``, at the reminder's configured
     *  wall-clock time.
     *  Returns ``null`` when no reminder is enabled.
     *
     *  The fallback anchor is the reminder's creation time — anchoring at
     *  epoch 0 (the old behaviour) flagged every never-yet-logged
     *  reminder as "overdue" the moment it was enabled. */
    private fun soonestReminderDueAt(
        reminders: List<PlantReminderEntity>,
        journal: List<PlantJournalEntity>,
    ): NextAction? {
        val active = reminders.filter { it.enabled }
        if (active.isEmpty()) return null
        return active.mapNotNull { reminder ->
            if (reminderTypeToJournalType(reminder.type) == null) return@mapNotNull null
            val anchor = reminderCadenceAnchor(reminder, journal)
            NextAction(
                reminder.type,
                CareSchedule.scheduledDueAt(
                    anchorMs = anchor,
                    intervalDays = reminder.intervalDays,
                    hour = reminder.hour,
                    minute = reminder.minute,
                ),
            )
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

    /** Later of the reminder's own schedule reset and its latest matching
     * care log. An old watering/fertilising log must not pull a newly enabled
     * or edited reminder back onto the plant's historical cadence. */
    internal fun reminderCadenceAnchor(
        reminder: PlantReminderEntity,
        journal: List<PlantJournalEntity>,
    ): Long {
        // Watering/fertilising are legacy single-row reminder types, so their
        // plant-wide quick journal actions can safely satisfy that one cadence.
        // Newer preset types may have several same-type rows; those use only
        // their row-specific createdAt anchor so completing one cannot move all.
        if (reminder.type !in LEGACY_JOURNAL_CADENCE_TYPES) return reminder.createdAt
        val journalType = reminderTypeToJournalType(reminder.type) ?: return reminder.createdAt
        val latestCareAt = journal.firstOrNull { it.type == journalType.value }?.timestamp
        return maxOf(reminder.createdAt, latestCareAt ?: reminder.createdAt)
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
        canonicalSpeciesUpdate: CanonicalSpeciesUpdate = CanonicalSpeciesUpdate.PRESERVE,
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
                    // A manual species correction must explicitly clear old
                    // identification fields; otherwise health/care/lunar keep
                    // classifying the plant under the stale scan species.
                    scientificName = when (canonicalSpeciesUpdate) {
                        CanonicalSpeciesUpdate.PRESERVE -> cleanScientific ?: existing.scientificName
                        CanonicalSpeciesUpdate.REPLACE -> cleanScientific
                    },
                    commonName = when (canonicalSpeciesUpdate) {
                        CanonicalSpeciesUpdate.PRESERVE -> cleanCommon ?: existing.commonName
                        CanonicalSpeciesUpdate.REPLACE -> cleanCommon
                    },
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

    /** Accepted scans only. Raw OOD/quality argmax rows remain stored for
     * correction feedback but cannot enter plant history, photos or sharing. */
    suspend fun getScansForPlant(plantId: Long): List<ScanEntity> = withContext(Dispatchers.IO) {
        scanDao.getScansForPlant(plantId)
            .filterNot(PersistedScanAbstentionPolicy::isAbstained)
    }

    /** Most-recent accepted scan for a plant, or null if there are none. */
    suspend fun getLatestScanForPlant(plantId: Long): ScanEntity? = withContext(Dispatchers.IO) {
        scanDao.getScansForPlant(plantId)
            .firstOrNull { !PersistedScanAbstentionPolicy.isAbstained(it) }
    }

    /** Shared guard for defensive UI filtering of already-loaded row lists. */
    fun isAbstainedScan(scan: ScanEntity): Boolean =
        PersistedScanAbstentionPolicy.isAbstained(scan)

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
     *  this right before posting the notification. Returns the new event
     *  id, or null when that occurrence already exists (worker replay or an
     *  in-app completion won the race), so no stale notification is posted. */
    suspend fun recordReminderFire(reminder: PlantReminderEntity): Long? =
        withContext(Dispatchers.IO) {
            database.withTransaction {
                val current = reminderDao.getById(reminder.id) ?: return@withTransaction null
                if (!current.enabled) return@withTransaction null
                val triggeredAt = System.currentTimeMillis()
                val journal = journalDao.getEntriesForPlant(current.plantId)
                val anchor = reminderCadenceAnchor(current, journal)
                val firedAt = CareQueue.latestOccurrenceBefore(
                    CareQueue.ReminderInput(
                        reminderId = current.id,
                        plantId = current.plantId,
                        type = current.type,
                        title = current.label ?: current.type,
                        intervalDays = current.intervalDays,
                        hour = current.hour,
                        minute = current.minute,
                        enabled = current.enabled,
                        anchorMs = anchor,
                    ),
                    cutoffExclusive = triggeredAt + 1L,
                )?.dueAt ?: return@withTransaction null
                val dayStart = CareQueue.dayStartMs(
                    CareQueue.epochDayOf(firedAt)
                )
                val dayEnd = CareQueue.dayStartMs(
                    CareQueue.epochDayOf(firedAt) + 1L
                )
                if (reminderEventDao.anyForOccurrence(current.id, dayStart, dayEnd) != null) {
                    return@withTransaction null
                }
                reminderEventDao.insert(
                    PlantReminderEventEntity(
                        reminderId = current.id,
                        plantId = current.plantId,
                        type = current.type,
                        firedAt = firedAt,
                        action = "pending",
                    )
                )
            }
        }

    /** Re-fire a snoozed notification using its original event row and
     * fired_at occurrence identity. If the user completed it in-app while it
     * slept, action is already done and the one-shot worker becomes a no-op. */
    suspend fun reactivateSnoozedReminderEvent(eventId: Long): Long? =
        withContext(Dispatchers.IO) {
            database.withTransaction {
                val event = reminderEventDao.getById(eventId) ?: return@withTransaction null
                if (event.action != "snoozed") return@withTransaction null
                val reminder = reminderDao.getById(event.reminderId) ?: return@withTransaction null
                if (!reminder.enabled) return@withTransaction null
                reminderEventDao.update(event.copy(action = "pending", doneAt = null))
                event.id
            }
        }

    /** Resolve a previously-fired event with an action (done /
     *  snoozed / skipped). "done" additionally back-fills a matching
     *  care journal entry so the user sees their record without an extra tap.
     *  Returns false for replays and invalid transitions. */
    suspend fun resolveReminderEvent(eventId: Long, action: String): Boolean =
        withContext(Dispatchers.IO) {
            database.withTransaction {
                if (action !in setOf("done", "snoozed", "skipped")) return@withTransaction false
                val event = reminderEventDao.getById(eventId) ?: return@withTransaction false
                if (event.action == action || event.action == "done") return@withTransaction false
                if (action != "done" && event.action != "pending") return@withTransaction false
                val now = System.currentTimeMillis()
                reminderEventDao.update(event.copy(action = action, doneAt = now))
                if (action == "done") {
                    reminderDao.getById(event.reminderId)?.let { reminder ->
                        reminderDao.upsert(reminder.copy(createdAt = now))
                    }
                    reminderTypeToJournalType(event.type)?.let { type ->
                        journalDao.insert(
                            PlantJournalEntity(
                                plantId = event.plantId,
                                type = type.value,
                                timestamp = now,
                            )
                        )
                    }
                }
                true
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
        val nextAction: NextAction? = null,
        val roomName: String? = null,
        val health: PlantHealth.Verdict = PlantHealth.Verdict(
            status = PlantHealth.Status.UNKNOWN,
            score = null,
            weekDelta = null,
            weekDeltaDays = null,
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

    /** Soonest "do something" hint for a plant, computed from the later of
     *  the reminder's schedule anchor and its newest matching care log.
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
        val LEGACY_JOURNAL_CADENCE_TYPES = setOf("watering", "fertilize")
        const val ADHERENCE_WINDOW_DAYS = 30L
    }
}
