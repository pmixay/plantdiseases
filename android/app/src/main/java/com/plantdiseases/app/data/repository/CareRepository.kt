package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.CareTaskDao
import com.plantdiseases.app.data.local.CareTaskEntity
import com.plantdiseases.app.data.local.PlantDao
import com.plantdiseases.app.data.local.PlantJournalDao
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
import kotlinx.coroutines.withContext

/**
 * Aggregation layer over [CareQueue]: loads reminders/steps/one-offs/journal
 * from Room, decorates the pure projection with what the UI needs (plant
 * name, room, thumbnail), and owns the write side (completing a task,
 * starting a treatment plan). PlantRepository stays the source of truth for
 * plant rows themselves; this repository is Garden-refactor-only.
 */
class CareRepository(
    private val plantDao: PlantDao,
    private val roomDao: RoomDao,
    private val scanDao: ScanDao,
    private val reminderDao: PlantReminderDao,
    private val reminderEventDao: PlantReminderEventDao,
    private val journalDao: PlantJournalDao,
    private val treatmentPlanDao: TreatmentPlanDao,
    private val treatmentStepDao: TreatmentStepDao,
    private val careTaskDao: CareTaskDao,
    private val plantRepository: PlantRepository,
) {
    data class CareTaskView(
        val task: CareQueue.CareTask,
        val plantName: String,
        val roomName: String?,
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
        data class OneOffDone(val taskId: Long) : CompletionRecord
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
        val thumbnailByPlantId = plants.associate { plant ->
            plant.id to (plant.coverPhotoUri ?: scanDao.getScansForPlant(plant.id).maxByOrNull { it.timestamp }?.imagePath)
        }

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
            reminderEventDao.recentForPlant(plant.id, limit = 50)
                .filter { it.action == "done" }
                .forEach { event ->
                    doneEvents += CareQueue.DoneEvent(
                        plantId = plant.id,
                        type = event.type,
                        epochDay = CareQueue.epochDayOf(event.firedAt),
                    )
                }
        }

        val stepRows = treatmentStepDao.getAllInRangeWithPlantId(fromMs, toMs)
        val stepCountByPlan = mutableMapOf<Long, Int>()
        val stepInputs = stepRows.map { row ->
            val count = stepCountByPlan.getOrPut(row.step.planId) { treatmentStepDao.countForPlan(row.step.planId) }
            CareQueue.StepInput(
                stepId = row.step.id,
                plantId = row.plantId,
                planId = row.step.planId,
                title = row.step.title,
                dueAtMs = row.step.dueAt,
                status = row.step.status,
                stepIndex = row.step.stepIndex,
                stepCount = count,
            )
        }

        val oneOffInputs = careTaskDao.inRange(fromMs, toMs).map { task ->
            CareQueue.OneOffInput(
                taskId = task.id,
                plantId = task.plantId,
                type = task.type,
                title = task.title,
                dueAtMs = task.dueAt,
                status = task.status,
            )
        }

        val tasks = CareQueue.project(reminderInputs, stepInputs, oneOffInputs, doneEvents, fromMs, toMs)
        return tasks.mapNotNull { task ->
            val plant = plantsById[task.plantId] ?: return@mapNotNull null
            CareTaskView(
                task = task,
                plantName = plant.name,
                roomName = plant.roomId?.let { roomNamesById[it] },
                thumbnailSource = thumbnailByPlantId[plant.id],
            )
        }
    }

    // ─── Completing / undoing ───────────────────────────────────────────

    suspend fun complete(view: CareTaskView): CompletionRecord = withContext(Dispatchers.IO) {
        when (val id = view.task.id) {
            is CareQueue.CareTask.TaskId.Recurring -> completeRecurring(id, view.task)
            is CareQueue.CareTask.TaskId.Step -> completeStep(id)
            is CareQueue.CareTask.TaskId.OneOff -> completeOneOff(id)
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
            is CompletionRecord.OneOffDone -> {
                val task = careTaskDao.getById(record.taskId) ?: return@withContext
                careTaskDao.update(task.copy(status = "pending", doneAt = null))
            }
        }
    }

    private suspend fun completeRecurring(
        id: CareQueue.CareTask.TaskId.Recurring,
        task: CareQueue.CareTask,
    ): CompletionRecord.RecurringDone {
        val reminder = reminderDao.getById(id.reminderId)
        val now = System.currentTimeMillis()
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

    private suspend fun completeOneOff(id: CareQueue.CareTask.TaskId.OneOff): CompletionRecord.OneOffDone {
        val task = careTaskDao.getById(id.taskId)
        if (task != null) {
            careTaskDao.update(task.copy(status = "done", doneAt = System.currentTimeMillis()))
        }
        return CompletionRecord.OneOffDone(id.taskId)
    }

    // ─── One-off tasks ───────────────────────────────────────────────────

    suspend fun addOneOff(
        plantId: Long,
        type: String,
        title: String,
        note: String?,
        dueAt: Long,
    ): Long = withContext(Dispatchers.IO) {
        careTaskDao.insert(
            CareTaskEntity(
                plantId = plantId,
                type = type,
                title = title.trim(),
                note = note?.trim()?.takeIf { it.isNotEmpty() },
                dueAt = dueAt,
            )
        )
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

    /** Has a plan already been started for this scan+disease, regardless of
     *  whether it's still active? Guards DetailExtraCardsController against
     *  re-creating a plan the user already finished or abandoned every time
     *  the care-plan card reloads. */
    suspend fun hasPlanForScan(plantId: Long, scanId: Long?, diseaseKey: String): Boolean =
        withContext(Dispatchers.IO) {
            treatmentPlanDao.existsForScanAndDisease(plantId, scanId, diseaseKey)
        }

    // ─── Single-row lookups (CareTaskActivity resolving one task by id) ───

    data class StepWithPlan(val step: TreatmentStepEntity, val plan: TreatmentPlanEntity)

    suspend fun getStep(stepId: Long): StepWithPlan? = withContext(Dispatchers.IO) {
        val step = treatmentStepDao.getById(stepId) ?: return@withContext null
        val plan = treatmentPlanDao.getById(step.planId) ?: return@withContext null
        StepWithPlan(step, plan)
    }

    suspend fun getOneOff(taskId: Long): CareTaskEntity? = withContext(Dispatchers.IO) {
        careTaskDao.getById(taskId)
    }

    suspend fun getReminder(reminderId: Long) = withContext(Dispatchers.IO) {
        reminderDao.getById(reminderId)
    }
}
