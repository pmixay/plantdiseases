package com.plantdiseases.app.data.repository

import androidx.room.withTransaction
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.local.PlantDao
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalDao
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.PlantReminderDao
import com.plantdiseases.app.data.local.PlantReminderEventDao
import com.plantdiseases.app.data.local.PlantReminderEventEntity
import com.plantdiseases.app.data.local.RoomDao
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.TreatmentPlanDao
import com.plantdiseases.app.data.local.TreatmentPlanEntity
import com.plantdiseases.app.data.local.TreatmentStepDao
import com.plantdiseases.app.data.local.TreatmentStepEntity
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.CareSchedule
import com.plantdiseases.app.util.TreatmentPlanFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Aggregation layer over [CareQueue]: loads reminders/treatment steps/journal
 * from Room, decorates the pure projection with what the UI needs (plant
 * name, room, thumbnail), and owns the write side (completing a task,
 * starting a treatment plan). PlantRepository stays the source of truth for
 * plant rows themselves; this repository is Garden-refactor-only.
 */
class CareRepository(
    private val database: AppDatabase,
    private val plantDao: PlantDao,
    private val scanDao: ScanDao,
    private val roomDao: RoomDao,
    private val reminderDao: PlantReminderDao,
    private val reminderEventDao: PlantReminderEventDao,
    private val journalDao: PlantJournalDao,
    private val treatmentPlanDao: TreatmentPlanDao,
    private val treatmentStepDao: TreatmentStepDao,
    private val plantRepository: PlantRepository,
) {
    private val treatmentPlanMutex = Mutex()
    private val recurringCompletionMutex = Mutex()

    data class CareTaskView(
        val task: CareQueue.CareTask,
        val plantName: String,
        val roomName: String?,
        /** The plant's cover photo, when it has one. Deliberately NOT a
         *  latest-scan fallback: resolving one cost a full scan-history read
         *  per plant on every calendar month swipe and day-view open, for a
         *  value no care surface renders (CareTaskActivity loads the plant
         *  itself and builds its own chip). */
        val thumbnailSource: String?,
    )

    data class PlanWithSteps(
        val plan: TreatmentPlanEntity,
        val steps: List<TreatmentStepEntity>,
    )

    /** Local-only payload for the accepted "create from scan" operation.
     * Keeping this beside [attachScanWithStoredChecklist] makes the plant row,
     * scan association and stored fallback checklist one durable commit. */
    data class NewPlantDraft(
        val name: String,
        val species: String?,
        val scientificName: String?,
        val commonName: String?,
        val roomId: Long?,
    )

    data class StoredChecklist(
        val carePlan: CarePlanResponse,
        val diseaseKey: String,
        val diseaseLabelEn: String,
        val diseaseLabelRu: String,
    )

    /** What complete() mutated, precisely enough for undo() to reverse
     *  exactly those rows — no heuristic re-derivation. */
    sealed interface CompletionRecord {
        data class RecurringDone(
            val eventId: Long,
            val reminderId: Long,
            val journalEntryId: Long?,
            val previousReminderCreatedAt: Long,
            val completionAnchorAt: Long,
            /** Non-null when completion resolved a notification-created row;
             * undo restores this exact prior disposition. Null means the
             * checklist inserted a new marker and undo deletes it. */
            val previousEvent: PlantReminderEventEntity? = null,
        ) : CompletionRecord
        data class StepDone(val stepId: Long, val autoCompletedPlanId: Long?) : CompletionRecord

        /** A duplicate completion changed no rows, so Undo must not remove a
         * completion (or journal entry) written by an earlier UI/action. */
        data object NoChange : CompletionRecord
    }

    suspend fun queue(fromMs: Long, toMs: Long): List<CareTaskView> = withContext(Dispatchers.IO) {
        buildQueue(fromMs, toMs)
    }

    suspend fun day(dayStartMs: Long): List<CareTaskView> = withContext(Dispatchers.IO) {
        val dayEndMs = CareQueue.dayStartMs(CareQueue.epochDayOf(dayStartMs) + 1L)
        val all = buildQueue(dayStartMs, dayEndMs)
        val tasksById = all.associateBy { it.task.id }
        CareQueue.forDay(all.map { it.task }, dayStartMs).mapNotNull { tasksById[it.id] }
    }

    /** Pending work for the home-screen "Do today" list: every treatment
     * step due through the end of today, plus one current occurrence for each
     * recurring reminder. This deliberately has no arbitrary look-back
     * window, while also avoiding hundreds of historical instances of the
     * same daily reminder. */
    suspend fun today(nowMs: Long = System.currentTimeMillis()): List<CareTaskView> =
        withContext(Dispatchers.IO) {
            val todayStart = CareQueue.dayStartMs(CareQueue.epochDayOf(nowMs))
            val todayEnd = CareQueue.dayStartMs(CareQueue.epochDayOf(nowMs) + 1L)
            val plants = plantDao.getAllPlants()
            val plantsById = plants.associateBy { it.id }
            val roomNamesById = roomDao.getAll().associate { it.id to it.name }
            val remindersByPlant = reminderDao.getAll().groupBy { it.plantId }
            val journalByPlant = journalDao.getAllEntries().groupBy { it.plantId }

            val recurring = buildList {
                for (plant in plants) {
                    val journal = journalByPlant[plant.id].orEmpty()
                    for (reminder in remindersByPlant[plant.id].orEmpty()) {
                        if (!reminder.enabled) continue
                        val anchor = plantRepository.reminderCadenceAnchor(reminder, journal)
                        val input = CareQueue.ReminderInput(
                            reminderId = reminder.id,
                            plantId = plant.id,
                            type = reminder.type,
                            title = reminder.label ?: reminder.type,
                            intervalDays = reminder.intervalDays,
                            hour = reminder.hour,
                            minute = reminder.minute,
                            enabled = true,
                            anchorMs = anchor,
                        )
                        val task = CareQueue.latestOccurrenceBefore(input, todayEnd) ?: continue
                        val occurrenceStart = CareQueue.dayStartMs(CareQueue.epochDayOf(task.dueAt))
                        val occurrenceEnd = CareQueue.dayStartMs(CareQueue.epochDayOf(task.dueAt) + 1L)
                        val completedOnOccurrence = reminderEventDao.doneForOccurrence(
                            reminder.id,
                            occurrenceStart,
                            occurrenceEnd,
                        ) != null
                        if (!completedOnOccurrence) {
                            add(task)
                        }
                    }
                }
            }

            val steps = treatmentStepDao.getActionableDueBeforeWithPlantId(todayEnd).map { row ->
                CareQueue.CareTask(
                    id = CareQueue.CareTask.TaskId.Step(row.step.id),
                    plantId = row.plantId,
                    type = "treatment",
                    title = row.step.title,
                    dueAt = row.step.dueAt,
                    status = CareQueue.CareTask.Status.PENDING,
                    stepIndex = row.step.stepIndex,
                )
            }

            (recurring + steps)
                .sortedWith(compareBy<CareQueue.CareTask>({ it.dueAt >= todayStart }, { it.dueAt }, { it.title }))
                .mapNotNull { task ->
                    val plant = plantsById[task.plantId] ?: return@mapNotNull null
                    CareTaskView(
                        task = task,
                        plantName = plant.name,
                        roomName = plant.roomId?.let { roomNamesById[it] },
                        thumbnailSource = plant.coverPhotoUri,
                    )
                }
        }

    suspend fun month(monthStartMs: Long, monthEndMs: Long, nowMs: Long): Map<Long, CareQueue.DayState> =
        withContext(Dispatchers.IO) {
            val all = buildQueue(monthStartMs, monthEndMs)
            CareQueue.monthSummary(all.map { it.task }, monthStartMs, monthEndMs, nowMs)
        }

    private suspend fun buildQueue(fromMs: Long, toMs: Long): List<CareTaskView> {
        val plants = plantDao.getAllPlants()
        val plantsById = plants.associateBy { it.id }
        val roomNamesById = roomDao.getAll().associate { it.id to it.name }

        val reminderInputs = mutableListOf<CareQueue.ReminderInput>()
        val doneEvents = mutableListOf<CareQueue.DoneEvent>()
        val historicalDoneTasks = mutableListOf<CareQueue.CareTask>()
        // Table-wide, not per plant: this runs on every calendar month swipe and
        // every day-view open. Both queries keep the ORDER BY of the per-plant
        // ones they replace, and groupBy preserves encounter order, so the
        // journal lookup below still finds the newest matching entry.
        val remindersByPlant = reminderDao.getAll().groupBy { it.plantId }
        val journalByPlant = journalDao.getAllEntries().groupBy { it.plantId }
        for (plant in plants) {
            val allReminders = remindersByPlant[plant.id].orEmpty()
            val reminders = allReminders.filter { it.enabled }
            val journal = journalByPlant[plant.id].orEmpty() // timestamp DESC
            for (reminder in reminders) {
                val anchor = plantRepository.reminderCadenceAnchor(reminder, journal)
                reminderInputs += CareQueue.ReminderInput(
                    reminderId = reminder.id,
                    plantId = plant.id,
                    type = reminder.type,
                    title = reminder.label ?: reminder.type,
                    intervalDays = reminder.intervalDays,
                    hour = reminder.hour,
                    minute = reminder.minute,
                    enabled = reminder.enabled,
                    anchorMs = anchor,
                )
            }
            val remindersById = allReminders.associateBy { it.id }
            // Current/future cadence uses the latest anchor above, but a later
            // care log must not erase an older completed occurrence from the
            // browsable calendar. Materialise persisted fired_at history as
            // tasks independently, then let it override a matching projection.
            reminderEventDao.doneBetween(plant.id, fromMs, toMs).forEach { event ->
                val firedDay = CareQueue.epochDayOf(event.firedAt)
                doneEvents += CareQueue.DoneEvent(plant.id, event.reminderId, event.type, firedDay)
                if (event.firedAt >= fromMs && event.firedAt < toMs) {
                    val reminder = remindersById[event.reminderId] ?: return@forEach
                    historicalDoneTasks += CareQueue.CareTask(
                        id = CareQueue.CareTask.TaskId.Recurring(event.reminderId, firedDay),
                        plantId = plant.id,
                        type = reminder.type,
                        title = reminder.label ?: reminder.type,
                        dueAt = event.firedAt,
                        status = CareQueue.CareTask.Status.DONE,
                    )
                }
            }
        }

        val stepRows = treatmentStepDao.getAllInRangeWithPlantId(fromMs, toMs)
        val planIds = stepRows.map { it.step.planId }.distinct()
        val stepCountByPlan: Map<Long, Int> = if (planIds.isEmpty()) {
            emptyMap()
        } else {
            treatmentStepDao.countsForPlans(planIds).associate { it.planId to it.stepCount }
        }
        val stepInputs = stepRows.map { row ->
            CareQueue.StepInput(
                stepId = row.step.id,
                plantId = row.plantId,
                planId = row.step.planId,
                title = row.step.title,
                dueAtMs = row.step.dueAt,
                status = row.step.status,
                stepIndex = row.step.stepIndex,
                stepCount = stepCountByPlan[row.step.planId] ?: (row.step.stepIndex + 1),
            )
        }

        val tasksById = linkedMapOf<CareQueue.CareTask.TaskId, CareQueue.CareTask>()
        CareQueue.project(reminderInputs, stepInputs, doneEvents, fromMs, toMs)
            .forEach { tasksById[it.id] = it }
        historicalDoneTasks.forEach { tasksById[it.id] = it }
        val tasks = tasksById.values
        return tasks.mapNotNull { task ->
            val plant = plantsById[task.plantId] ?: return@mapNotNull null
            CareTaskView(
                task = task,
                plantName = plant.name,
                roomName = plant.roomId?.let { roomNamesById[it] },
                thumbnailSource = plant.coverPhotoUri,
            )
        }
    }

    // ─── Completing / undoing ───────────────────────────────────────────

    suspend fun complete(view: CareTaskView): CompletionRecord = withContext(Dispatchers.IO) {
        when (val id = view.task.id) {
            is CareQueue.CareTask.TaskId.Recurring -> recurringCompletionMutex.withLock {
                database.withTransaction { completeRecurring(id, view.task) }
            }
            is CareQueue.CareTask.TaskId.Step -> treatmentPlanMutex.withLock {
                database.withTransaction { completeStep(id) }
            }
        }
    }

    suspend fun undo(record: CompletionRecord) = withContext(Dispatchers.IO) {
        when (record) {
            is CompletionRecord.RecurringDone -> recurringCompletionMutex.withLock {
                database.withTransaction {
                    if (record.previousEvent == null) {
                        reminderEventDao.deleteById(record.eventId)
                    } else {
                        reminderEventDao.update(record.previousEvent)
                    }
                    record.journalEntryId?.let { journalDao.deleteById(it) }
                    reminderDao.getById(record.reminderId)
                        ?.takeIf { it.createdAt == record.completionAnchorAt }
                        ?.let { reminder ->
                            reminderDao.upsert(reminder.copy(createdAt = record.previousReminderCreatedAt))
                        }
                }
            }
            is CompletionRecord.StepDone -> treatmentPlanMutex.withLock {
                database.withTransaction {
                    val step = treatmentStepDao.getById(record.stepId) ?: return@withTransaction
                    val autoCompletedPlan = record.autoCompletedPlanId?.let { planId ->
                        treatmentPlanDao.getById(planId)
                    }
                    if (autoCompletedPlan != null) {
                        val otherActive = treatmentPlanDao.getActiveForPlant(autoCompletedPlan.plantId)
                        if (otherActive != null && otherActive.id != autoCompletedPlan.id) {
                            return@withTransaction
                        }
                    }
                    treatmentStepDao.update(step.copy(status = "pending", doneAt = null))
                    autoCompletedPlan?.let { plan ->
                        treatmentPlanDao.update(plan.copy(status = "active", completedAt = null))
                    }
                }
            }
            CompletionRecord.NoChange -> Unit
        }
    }

    /** A recurring occurrence has no row of its own, so "done" is recorded as
     *  a `plant_reminder_events` row whose `fired_at` is the occurrence's
     *  original due instant and whose `done_at` is now. Both timestamps
     *  matter: firedAt preserves occurrence identity, while doneAt drives
     *  history/adherence and the matching care journal re-anchors the next
     *  occurrence one full interval into the future. */
    private suspend fun completeRecurring(
        id: CareQueue.CareTask.TaskId.Recurring,
        task: CareQueue.CareTask,
    ): CompletionRecord {
        val now = System.currentTimeMillis()
        // Idempotent: the day view's checkbox and the task-detail screen can
        // both reach the same occurrence, and a second completion would write
        // a duplicate journal entry (user-visible) plus a duplicate done
        // event that inflates PlantHealth adherence.
        val dayStart = CareQueue.dayStartMs(id.epochDay)
        // Match on fired_at only. doneBetween's predicate is `fired_at in range
        // OR done_at in range`, and a LATE completion stamps done_at = now — so
        // a plain range hit would return yesterday's occurrence when asked about
        // today's, and make today's uncompletable. An occurrence's identity is
        // its reminder id plus its fired day — matching on type instead would
        // collide across two same-type reminders on one plant, which the schema
        // deliberately allows (see PlantReminderEntity's index comment).
        val dayEnd = CareQueue.dayStartMs(id.epochDay + 1L)
        val alreadyDone = reminderEventDao
            .doneBetween(task.plantId, dayStart, dayEnd)
            .firstOrNull {
                it.reminderId == id.reminderId && CareQueue.epochDayOf(it.firedAt) == id.epochDay
            }
        if (alreadyDone != null) {
            return CompletionRecord.NoChange
        }
        val reminder = reminderDao.getById(id.reminderId) ?: return CompletionRecord.NoChange
        reminderDao.upsert(reminder.copy(createdAt = now))
        val journalType = plantRepository.reminderTypeToJournalType(reminder.type)
        val journalEntryId = journalType?.let { type -> logCareByType(task.plantId, type, now) }
        val pending = reminderEventDao.pendingForOccurrence(
            reminderId = id.reminderId,
            fromMs = dayStart,
            toMs = dayEnd,
        )
        if (pending != null) {
            reminderEventDao.update(pending.copy(action = "done", doneAt = now))
            return CompletionRecord.RecurringDone(
                eventId = pending.id,
                reminderId = reminder.id,
                journalEntryId = journalEntryId,
                previousReminderCreatedAt = reminder.createdAt,
                completionAnchorAt = now,
                previousEvent = pending,
            )
        }
        val eventId = reminderEventDao.insert(
            PlantReminderEventEntity(
                reminderId = id.reminderId,
                plantId = task.plantId,
                type = task.type,
                firedAt = task.dueAt,
                action = "done",
                doneAt = now,
            )
        )
        return CompletionRecord.RecurringDone(
            eventId = eventId,
            reminderId = reminder.id,
            journalEntryId = journalEntryId,
            previousReminderCreatedAt = reminder.createdAt,
            completionAnchorAt = now,
        )
    }

    private suspend fun logCareByType(
        plantId: Long,
        type: PlantRepository.JournalType,
        timestamp: Long,
    ): Long? = if (type == PlantRepository.JournalType.NOTE || type == PlantRepository.JournalType.PHOTO) {
        null
    } else {
        journalDao.insert(PlantJournalEntity(plantId = plantId, type = type.value, timestamp = timestamp))
    }

    private suspend fun completeStep(id: CareQueue.CareTask.TaskId.Step): CompletionRecord {
        val step = treatmentStepDao.getById(id.stepId)
            ?: return CompletionRecord.NoChange
        if (step.status != "pending") return CompletionRecord.NoChange
        val owningPlan = treatmentPlanDao.getById(step.planId)
            ?.takeIf { it.status == "active" }
            ?: return CompletionRecord.NoChange
        treatmentStepDao.update(step.copy(status = "done", doneAt = System.currentTimeMillis()))

        val siblings = treatmentStepDao.getForPlan(step.planId)
        val stillPending = siblings.any { it.id != step.id && it.status == "pending" }
        var autoCompletedPlanId: Long? = null
        if (!stillPending) {
            treatmentPlanDao.update(
                owningPlan.copy(status = "completed", completedAt = System.currentTimeMillis())
            )
            autoCompletedPlanId = owningPlan.id
        }
        return CompletionRecord.StepDone(step.id, autoCompletedPlanId)
    }

    // ─── Treatment plans ─────────────────────────────────────────────────

    /** At most one active plan per plant — any existing active plan for
     *  this plant is abandoned first. Returns null when the care plan
     *  carries no treatment steps (TreatmentPlanFactory.build returned
     *  null), in which case no row is written at all.
     *
     *  Kept as a standalone entry point / test seam: this wrapper is where
     *  the mutex + `withTransaction` envelope lives. Production reaches the
     *  same work through [ensureTreatmentPlanForScan], which calls
     *  `startTreatmentPlanLocked` directly because it already holds both. */
    suspend fun startTreatmentPlan(
        plantId: Long,
        scanId: Long?,
        carePlan: CarePlanResponse,
        diseaseKey: String,
        diseaseLabelEn: String,
        diseaseLabelRu: String,
    ): Long? = withContext(Dispatchers.IO) {
        treatmentPlanMutex.withLock {
            database.withTransaction {
                startTreatmentPlanLocked(
                    plantId,
                    scanId,
                    carePlan,
                    diseaseKey,
                    diseaseLabelEn,
                    diseaseLabelRu,
                )
            }
        }
    }

    private suspend fun startTreatmentPlanLocked(
        plantId: Long,
        scanId: Long?,
        carePlan: CarePlanResponse,
        diseaseKey: String,
        diseaseLabelEn: String,
        diseaseLabelRu: String,
    ): Long? {
        val built = TreatmentPlanFactory.build(
            plan = carePlan,
            plantId = plantId,
            scanId = scanId,
            diseaseKey = diseaseKey,
            diseaseLabelEn = diseaseLabelEn,
            diseaseLabelRu = diseaseLabelRu,
            startedAtMs = System.currentTimeMillis(),
        ) ?: return null

        treatmentPlanDao.getActiveForPlant(plantId)?.let { existing ->
            treatmentPlanDao.update(existing.copy(status = "abandoned", completedAt = System.currentTimeMillis()))
        }

        val planId = treatmentPlanDao.insert(built.plan)
        treatmentStepDao.insertAll(built.steps.map { it.copy(planId = planId) })
        return planId
    }

    /** Idempotently materialise the checklist for a scan. The mutex closes
     * the race between the cached care-plan render and a simultaneous
     * onResume/retry render. A same-diagnosis active plan is kept (progress is
     * never reset), and its immutable source scan association is preserved. */
    suspend fun ensureTreatmentPlanForScan(
        plantId: Long,
        scanId: Long,
        carePlan: CarePlanResponse,
        diseaseKey: String,
        diseaseLabelEn: String,
        diseaseLabelRu: String,
    ): Long? = withContext(Dispatchers.IO) {
        treatmentPlanMutex.withLock {
            database.withTransaction {
                ensureTreatmentPlanForScanLocked(
                    plantId,
                    scanId,
                    carePlan,
                    diseaseKey,
                    diseaseLabelEn,
                    diseaseLabelRu,
                )
            }
        }
    }

    private suspend fun ensureTreatmentPlanForScanLocked(
        plantId: Long,
        scanId: Long,
        carePlan: CarePlanResponse,
        diseaseKey: String,
        diseaseLabelEn: String,
        diseaseLabelRu: String,
    ): Long? {
        val existingForScan = treatmentPlanDao.getAllForPlant(plantId)
            .firstOrNull { it.scanId == scanId }
        if (existingForScan != null) {
            if (existingForScan.status != "active") return null
            val oldSteps = treatmentStepDao.getForPlan(existingForScan.id)
            // Never replace in-progress work. Before the first Done tap,
            // however, a compact stored-scan fallback may be upgraded to
            // the richer server care plan (checkpoints/products/conditions)
            // without losing user state.
            if (oldSteps.any { it.status != "pending" }) return null
            val enriched = TreatmentPlanFactory.build(
                plan = carePlan,
                plantId = plantId,
                scanId = scanId,
                diseaseKey = diseaseKey,
                diseaseLabelEn = diseaseLabelEn,
                diseaseLabelRu = diseaseLabelRu,
                startedAtMs = existingForScan.startedAt,
            ) ?: return null
            val sameSteps = oldSteps.map(::stepContentKey) == enriched.steps.map(::stepContentKey)
            val sameConditions = existingForScan.conditionsJson == enriched.plan.conditionsJson
            if (sameSteps && sameConditions) return null

            treatmentStepDao.deleteForPlan(existingForScan.id)
            treatmentStepDao.insertAll(enriched.steps.map { it.copy(planId = existingForScan.id) })
            treatmentPlanDao.update(
                existingForScan.copy(
                    diseaseKey = diseaseKey,
                    diseaseLabelEn = diseaseLabelEn,
                    diseaseLabelRu = diseaseLabelRu,
                    conditionsJson = enriched.plan.conditionsJson,
                )
            )
            return existingForScan.id
        }
        val active = treatmentPlanDao.getActiveForPlant(plantId)
        if (active?.diseaseKey == diseaseKey) return null
        return startTreatmentPlanLocked(
            plantId = plantId,
            scanId = scanId,
            carePlan = carePlan,
            diseaseKey = diseaseKey,
            diseaseLabelEn = diseaseLabelEn,
            diseaseLabelRu = diseaseLabelRu,
        )
    }

    /** Finish an accepted add-to-garden action even if Result rotates or
     * closes. All durable local rows commit atomically; optional server
     * enrichment deliberately remains outside this transaction. */
    suspend fun attachScanWithStoredChecklist(
        scanId: Long,
        existingPlantId: Long? = null,
        newPlant: NewPlantDraft? = null,
        storedChecklist: StoredChecklist? = null,
    ): Long? = withContext(Dispatchers.IO + NonCancellable) {
        treatmentPlanMutex.withLock {
            database.withTransaction {
                val scan = scanDao.getScanById(scanId) ?: return@withTransaction null
                if (PersistedScanAbstentionPolicy.isAbstained(scan)) {
                    return@withTransaction null
                }
                val plantId = existingPlantId
                    ?: scan.plantId
                    ?: newPlant?.let { draft ->
                        plantDao.insert(
                            PlantEntity(
                                name = draft.name.trim(),
                                species = draft.species.cleanSpeciesValue(),
                                scientificName = draft.scientificName.cleanSpeciesValue(),
                                commonName = draft.commonName.cleanSpeciesValue(),
                                roomId = draft.roomId,
                            )
                        )
                    }
                    ?: return@withTransaction null
                if (plantDao.getPlantById(plantId) == null) return@withTransaction null
                if (scan.plantId != plantId) scanDao.assignScanToPlant(scanId, plantId)
                storedChecklist?.let { checklist ->
                    ensureTreatmentPlanForScanLocked(
                        plantId = plantId,
                        scanId = scanId,
                        carePlan = checklist.carePlan,
                        diseaseKey = checklist.diseaseKey,
                        diseaseLabelEn = checklist.diseaseLabelEn,
                        diseaseLabelRu = checklist.diseaseLabelRu,
                    )
                }
                plantId
            }
        }
    }

    private fun String?.cleanSpeciesValue(): String? = this?.trim()?.takeIf { it.isNotEmpty() }

    private fun stepContentKey(step: TreatmentStepEntity): List<Any?> = listOf(
        step.title,
        step.detail,
        step.bullets,
        step.productsJson,
        step.dueAt,
    )

    suspend fun activePlan(plantId: Long): PlanWithSteps? = withContext(Dispatchers.IO) {
        val plan = treatmentPlanDao.getActiveForPlant(plantId) ?: return@withContext null
        PlanWithSteps(plan, treatmentStepDao.getForPlan(plan.id))
    }

    /** The active plan, or — when there is none — the most recently completed
     *  one within [windowMs] (never an abandoned one), with its steps loaded.
     *  The plant-detail card renders a "treatment finished" line off this, so
     *  the card doesn't silently vanish the moment the last step is ticked. */
    suspend fun activeOrRecentlyCompletedPlan(
        plantId: Long,
        windowMs: Long = 7 * CareSchedule.DAY_MS,
    ): PlanWithSteps? = withContext(Dispatchers.IO) {
        val active = treatmentPlanDao.getActiveForPlant(plantId)
        if (active != null) {
            return@withContext PlanWithSteps(active, treatmentStepDao.getForPlan(active.id))
        }
        val cutoff = System.currentTimeMillis() - windowMs
        // getAllForPlant is ORDER BY started_at DESC and carries every status.
        val recent = treatmentPlanDao.getAllForPlant(plantId)
            .firstOrNull { it.status == "completed" && (it.completedAt ?: 0L) >= cutoff }
            ?: return@withContext null
        PlanWithSteps(recent, treatmentStepDao.getForPlan(recent.id))
    }

    // ─── Single-row lookups (CareTaskActivity resolving one task by id) ───

    data class StepWithPlan(
        val step: TreatmentStepEntity,
        val plan: TreatmentPlanEntity,
        val stepCount: Int,
    )

    suspend fun getStep(stepId: Long): StepWithPlan? = withContext(Dispatchers.IO) {
        val step = treatmentStepDao.getById(stepId) ?: return@withContext null
        val plan = treatmentPlanDao.getById(step.planId) ?: return@withContext null
        // Do not derive this through activePlan(): completed-plan rows are
        // still browsable from the calendar history, but activePlan() quite
        // correctly excludes them. Falling back to stepIndex + 1 made an old
        // "Step 1 of 3" render as "Step 1 of 1" after plan completion.
        StepWithPlan(step, plan, treatmentStepDao.getForPlan(plan.id).size)
    }

    suspend fun getReminder(reminderId: Long) = withContext(Dispatchers.IO) {
        reminderDao.getById(reminderId)
    }
}
