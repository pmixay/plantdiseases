package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.PlantDao
import com.plantdiseases.app.data.local.PlantJournalDao
import com.plantdiseases.app.data.local.PlantReminderDao
import com.plantdiseases.app.data.local.PlantReminderEventDao
import com.plantdiseases.app.data.local.PlantReminderEventEntity
import com.plantdiseases.app.data.local.RoomDao
import com.plantdiseases.app.data.local.TreatmentPlanDao
import com.plantdiseases.app.data.local.TreatmentPlanEntity
import com.plantdiseases.app.data.local.TreatmentStepDao
import com.plantdiseases.app.data.local.TreatmentStepEntity
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.CareSchedule
import com.plantdiseases.app.util.TreatmentPlanFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Aggregation layer over [CareQueue]: loads reminders/treatment steps/journal
 * from Room, decorates the pure projection with what the UI needs (plant
 * name, room, thumbnail), and owns the write side (completing a task,
 * starting a treatment plan). PlantRepository stays the source of truth for
 * plant rows themselves; this repository is Garden-refactor-only.
 */
class CareRepository(
    private val plantDao: PlantDao,
    private val roomDao: RoomDao,
    private val reminderDao: PlantReminderDao,
    private val reminderEventDao: PlantReminderEventDao,
    private val journalDao: PlantJournalDao,
    private val treatmentPlanDao: TreatmentPlanDao,
    private val treatmentStepDao: TreatmentStepDao,
    private val plantRepository: PlantRepository,
) {
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

    /** What complete() mutated, precisely enough for undo() to reverse
     *  exactly those rows — no heuristic re-derivation. */
    sealed interface CompletionRecord {
        data class RecurringDone(val eventId: Long, val journalEntryId: Long?) : CompletionRecord
        data class StepDone(val stepId: Long, val autoCompletedPlanId: Long?) : CompletionRecord
    }

    suspend fun queue(fromMs: Long, toMs: Long): List<CareTaskView> = withContext(Dispatchers.IO) {
        buildQueue(fromMs, toMs)
    }

    suspend fun day(dayStartMs: Long): List<CareTaskView> = withContext(Dispatchers.IO) {
        val all = buildQueue(dayStartMs, dayStartMs + CareSchedule.DAY_MS)
        val tasksById = all.associateBy { it.task.id }
        CareQueue.forDay(all.map { it.task }, dayStartMs).mapNotNull { tasksById[it.id] }
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
        for (plant in plants) {
            val reminders = reminderDao.getForPlant(plant.id).filter { it.enabled }
            if (reminders.isEmpty()) continue
            val journal = journalDao.getEntriesForPlant(plant.id) // ORDER BY timestamp DESC
            for (reminder in reminders) {
                val journalType = plantRepository.reminderTypeToJournalType(reminder.type)
                val anchor = journalType
                    ?.let { type -> journal.firstOrNull { it.type == type.value }?.timestamp }
                    ?: plant.createdAt
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
            // completeRecurring stamps `fired_at` with the occurrence's
            // ORIGINAL due instant while `done_at` is when the user actually
            // ticked it. For a journal-mapped type the anchor above jumps to
            // that journal entry, so occurrence 0 lands on the completion day
            // and would show pending again on the very day it was completed —
            // hence the second marker. For the types with no journal mapping
            // the anchor is pinned to plant.createdAt and never moves, so the
            // cadence is unchanged and a completion-day marker would falsely
            // clear whatever genuine occurrence happens to fall there.
            reminderEventDao.doneBetween(plant.id, fromMs, toMs).forEach { event ->
                val firedDay = CareQueue.epochDayOf(event.firedAt)
                doneEvents += CareQueue.DoneEvent(plant.id, event.type, firedDay)
                if (plantRepository.reminderTypeToJournalType(event.type) == null) return@forEach
                val doneDay = event.doneAt?.let { CareQueue.epochDayOf(it) }
                if (doneDay != null && doneDay != firedDay) {
                    doneEvents += CareQueue.DoneEvent(plant.id, event.type, doneDay)
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

        val tasks = CareQueue.project(reminderInputs, stepInputs, doneEvents, fromMs, toMs)
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
            is CareQueue.CareTask.TaskId.Recurring -> completeRecurring(id, view.task)
            is CareQueue.CareTask.TaskId.Step -> completeStep(id)
        }
    }

    suspend fun undo(record: CompletionRecord) = withContext(Dispatchers.IO) {
        when (record) {
            is CompletionRecord.RecurringDone -> {
                reminderEventDao.deleteById(record.eventId)
                record.journalEntryId?.let { plantRepository.deleteJournalEntry(it) }
            }
            is CompletionRecord.StepDone -> {
                val step = treatmentStepDao.getById(record.stepId) ?: return@withContext
                treatmentStepDao.update(step.copy(status = "pending", doneAt = null))
                record.autoCompletedPlanId?.let { planId ->
                    val plan = treatmentPlanDao.getById(planId) ?: return@let
                    treatmentPlanDao.update(plan.copy(status = "active", completedAt = null))
                }
            }
        }
    }

    /** A recurring occurrence has no row of its own, so "done" is recorded as
     *  a `plant_reminder_events` row whose `fired_at` is the occurrence's
     *  original due instant and whose `done_at` is now. Both timestamps
     *  matter: buildQueue marks *both* days done, because completing late
     *  re-anchors the reminder onto today and would otherwise re-spawn the
     *  task as pending on the very day it was completed. */
    private suspend fun completeRecurring(
        id: CareQueue.CareTask.TaskId.Recurring,
        task: CareQueue.CareTask,
    ): CompletionRecord.RecurringDone {
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
        // its fired day.
        val alreadyDone = reminderEventDao
            .doneBetween(task.plantId, dayStart, dayStart + CareSchedule.DAY_MS)
            .firstOrNull { it.type == task.type && CareQueue.epochDayOf(it.firedAt) == id.epochDay }
        if (alreadyDone != null) {
            // Undo still reverses this occurrence's marker; the earlier
            // completion's journal entry is not this call's to delete.
            return CompletionRecord.RecurringDone(alreadyDone.id, journalEntryId = null)
        }
        val reminder = reminderDao.getById(id.reminderId)
        val journalEntryId = reminder?.let {
            val journalType = plantRepository.reminderTypeToJournalType(it.type)
            journalType?.let { type -> logCareByType(task.plantId, type) }
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
        return CompletionRecord.RecurringDone(eventId, journalEntryId)
    }

    private suspend fun logCareByType(plantId: Long, type: PlantRepository.JournalType): Long? =
        when (type) {
            PlantRepository.JournalType.WATERING -> plantRepository.addWatering(plantId, text = null)
            PlantRepository.JournalType.FERTILISING -> plantRepository.addFertilising(plantId, text = null)
            PlantRepository.JournalType.MISTING -> plantRepository.addMisting(plantId, text = null)
            PlantRepository.JournalType.REPOTTING -> plantRepository.addRepotting(plantId, text = null)
            PlantRepository.JournalType.PRUNING -> plantRepository.addPruning(plantId, text = null)
            else -> null
        }

    private suspend fun completeStep(id: CareQueue.CareTask.TaskId.Step): CompletionRecord.StepDone {
        val step = treatmentStepDao.getById(id.stepId)
            ?: return CompletionRecord.StepDone(id.stepId, autoCompletedPlanId = null)
        treatmentStepDao.update(step.copy(status = "done", doneAt = System.currentTimeMillis()))

        val siblings = treatmentStepDao.getForPlan(step.planId)
        val stillPending = siblings.any { it.id != step.id && it.status == "pending" }
        var autoCompletedPlanId: Long? = null
        if (!stillPending) {
            val plan = treatmentPlanDao.getById(step.planId)
            if (plan != null && plan.status == "active") {
                treatmentPlanDao.update(plan.copy(status = "completed", completedAt = System.currentTimeMillis()))
                autoCompletedPlanId = plan.id
            }
        }
        return CompletionRecord.StepDone(step.id, autoCompletedPlanId)
    }

    // ─── Treatment plans ─────────────────────────────────────────────────

    /** At most one active plan per plant — any existing active plan for
     *  this plant is abandoned first. Returns null when the care plan
     *  carries no treatment steps (TreatmentPlanFactory.build returned
     *  null), in which case no row is written at all. */
    suspend fun startTreatmentPlan(
        plantId: Long,
        scanId: Long?,
        carePlan: CarePlanResponse,
        diseaseKey: String,
        diseaseLabelEn: String,
        diseaseLabelRu: String,
    ): Long? = withContext(Dispatchers.IO) {
        val built = TreatmentPlanFactory.build(
            plan = carePlan,
            plantId = plantId,
            scanId = scanId,
            diseaseKey = diseaseKey,
            diseaseLabelEn = diseaseLabelEn,
            diseaseLabelRu = diseaseLabelRu,
            startedAtMs = System.currentTimeMillis(),
        ) ?: return@withContext null

        treatmentPlanDao.getActiveForPlant(plantId)?.let { existing ->
            treatmentPlanDao.update(existing.copy(status = "abandoned", completedAt = System.currentTimeMillis()))
        }

        val planId = treatmentPlanDao.insert(built.plan)
        treatmentStepDao.insertAll(built.steps.map { it.copy(planId = planId) })
        planId
    }

    suspend fun activePlan(plantId: Long): PlanWithSteps? = withContext(Dispatchers.IO) {
        val plan = treatmentPlanDao.getActiveForPlant(plantId) ?: return@withContext null
        PlanWithSteps(plan, treatmentStepDao.getForPlan(plan.id))
    }

    /** Any plan — active, completed or abandoned — already derived from this
     *  scan. [activePlan] filters `status = 'active'` in SQL, so it goes blind
     *  the moment the user ticks the last step; callers that decide whether to
     *  materialise a plan need this second guard or they silently re-create a
     *  finished plan with its progress reset. */
    suspend fun hasPlanForScan(plantId: Long, scanId: Long): Boolean = withContext(Dispatchers.IO) {
        treatmentPlanDao.getAllForPlant(plantId).any { it.scanId == scanId }
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

    data class StepWithPlan(val step: TreatmentStepEntity, val plan: TreatmentPlanEntity)

    suspend fun getStep(stepId: Long): StepWithPlan? = withContext(Dispatchers.IO) {
        val step = treatmentStepDao.getById(stepId) ?: return@withContext null
        val plan = treatmentPlanDao.getById(step.planId) ?: return@withContext null
        StepWithPlan(step, plan)
    }

    suspend fun getReminder(reminderId: Long) = withContext(Dispatchers.IO) {
        reminderDao.getById(reminderId)
    }
}
