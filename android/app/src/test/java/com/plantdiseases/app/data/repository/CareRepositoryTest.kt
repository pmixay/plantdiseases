package com.plantdiseases.app.data.repository

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.data.local.PlantReminderEventEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CarePlanCheckpoint
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.CareSchedule
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CareRepositoryTest {

    private lateinit var db: AppDatabase
    private lateinit var plantRepository: PlantRepository
    private lateinit var careRepository: CareRepository

    @Before
    fun setup() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        plantRepository = PlantRepository(
            db.plantDao(),
            db.plantJournalDao(),
            db.scanDao(),
            db.plantReminderDao(),
            db.plantReminderEventDao(),
            db.chatMessageDao(),
            db.roomDao(),
            db.treatmentPlanDao(),
            db.treatmentStepDao(),
            db,
        )
        careRepository = CareRepository(
            database = db,
            plantDao = db.plantDao(),
            scanDao = db.scanDao(),
            roomDao = db.roomDao(),
            reminderDao = db.plantReminderDao(),
            reminderEventDao = db.plantReminderEventDao(),
            journalDao = db.plantJournalDao(),
            treatmentPlanDao = db.treatmentPlanDao(),
            treatmentStepDao = db.treatmentStepDao(),
            plantRepository = plantRepository,
        )
    }

    @After
    fun teardown() {
        db.close()
    }

    @Test
    fun completeRecurring_resolvesPendingNotificationRow_andUndoRestoresIt() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val dueAt = System.currentTimeMillis()
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "watering",
                intervalDays = 7,
                enabled = true,
                createdAt = dueAt - 7L * CareSchedule.DAY_MS,
            )
        )
        val pendingId = db.plantReminderEventDao().insert(
            PlantReminderEventEntity(
                reminderId = reminderId,
                plantId = plantId,
                type = "watering",
                firedAt = dueAt,
                action = "pending",
            )
        )
        val view = recurringView(plantId, reminderId, dueAt)

        val record = careRepository.complete(view)

        assertThat(record).isInstanceOf(CareRepository.CompletionRecord.RecurringDone::class.java)
        assertThat(db.plantReminderEventDao().getById(pendingId)?.action).isEqualTo("done")
        assertThat(db.plantReminderEventDao().recentForPlant(plantId)).hasSize(1)
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).hasSize(1)

        val duplicate = careRepository.complete(view)
        assertThat(duplicate).isEqualTo(CareRepository.CompletionRecord.NoChange)
        assertThat(db.plantReminderEventDao().recentForPlant(plantId)).hasSize(1)
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).hasSize(1)

        careRepository.undo(record)

        val restored = db.plantReminderEventDao().getById(pendingId)
        assertThat(restored?.action).isEqualTo("pending")
        assertThat(restored?.doneAt).isNull()
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).isEmpty()
        assertThat(db.plantReminderDao().getById(reminderId)?.createdAt)
            .isEqualTo(dueAt - 7L * CareSchedule.DAY_MS)
    }

    @Test
    fun newlyEnabledOrEditedReminderWithOldCareLog_waitsAFullIntervalInQueueAndSummary() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(
            PlantEntity(name = "Old fig", createdAt = now - 500L * CareSchedule.DAY_MS)
        )
        db.plantJournalDao().insert(
            PlantJournalEntity(
                plantId = plantId,
                type = PlantRepository.JournalType.WATERING.value,
                timestamp = now - 100L * CareSchedule.DAY_MS,
            )
        )
        db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "watering",
                intervalDays = 7,
                hour = 9,
                enabled = true,
                createdAt = now,
            )
        )

        assertThat(careRepository.today(now)).isEmpty()
        assertThat(plantRepository.getPlants().single().nextAction?.dueAt).isEqualTo(
            CareSchedule.scheduledDueAt(
                anchorMs = now,
                intervalDays = 7,
                hour = 9,
                minute = 0,
            )
        )
    }

    @Test
    fun notificationAndChecklistDoneRace_writesOneEventAndOneJournalEntry() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val dueAt = System.currentTimeMillis()
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "watering",
                intervalDays = 7,
                enabled = true,
                createdAt = dueAt - 7L * CareSchedule.DAY_MS,
            )
        )
        val eventId = db.plantReminderEventDao().insert(
            PlantReminderEventEntity(
                reminderId = reminderId,
                plantId = plantId,
                type = "watering",
                firedAt = dueAt,
            )
        )

        coroutineScope {
            val checklist = async { careRepository.complete(recurringView(plantId, reminderId, dueAt)) }
            val notification = async { plantRepository.resolveReminderEvent(eventId, "done") }
            checklist.await()
            notification.await()
        }

        assertThat(db.plantReminderEventDao().recentForPlant(plantId)).hasSize(1)
        assertThat(db.plantReminderEventDao().getById(eventId)?.action).isEqualTo("done")
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).hasSize(1)
        assertThat(plantRepository.resolveReminderEvent(eventId, "done")).isFalse()
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).hasSize(1)
    }

    @Test
    fun snoozeRefire_preservesOriginalOccurrenceAndDoneRemainsSingle() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "watering",
                intervalDays = 7,
                enabled = true,
                createdAt = now - 14L * CareSchedule.DAY_MS,
            )
        )
        val reminder = db.plantReminderDao().getById(reminderId)!!
        val eventId = plantRepository.recordReminderFire(reminder)!!
        val originalFiredAt = db.plantReminderEventDao().getById(eventId)!!.firedAt

        assertThat(plantRepository.resolveReminderEvent(eventId, "snoozed")).isTrue()
        assertThat(plantRepository.reactivateSnoozedReminderEvent(eventId)).isEqualTo(eventId)
        assertThat(db.plantReminderEventDao().getById(eventId)?.firedAt).isEqualTo(originalFiredAt)

        careRepository.complete(recurringView(plantId, reminderId, originalFiredAt))

        assertThat(db.plantReminderEventDao().recentForPlant(plantId)).hasSize(1)
        assertThat(db.plantReminderEventDao().getById(eventId)?.action).isEqualTo("done")
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).hasSize(1)
        assertThat(plantRepository.resolveReminderEvent(eventId, "done")).isFalse()
        assertThat(db.plantJournalDao().getEntriesForPlant(plantId)).hasSize(1)
    }

    @Test
    fun reanchoredReminder_suppressesAlreadyRunningOldWorker() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "check",
                intervalDays = 7,
                enabled = true,
                createdAt = now,
            )
        )

        assertThat(plantRepository.recordReminderFire(db.plantReminderDao().getById(reminderId)!!)).isNull()
        assertThat(db.plantReminderEventDao().recentForPlant(plantId)).isEmpty()
    }

    @Test
    fun nonJournalReminderLateCompletion_reanchorsQueueToCompletion() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "check",
                intervalDays = 7,
                hour = 9,
                enabled = true,
                createdAt = now - 14L * CareSchedule.DAY_MS,
            )
        )
        val oldDueAt = now - CareSchedule.DAY_MS
        val record = careRepository.complete(recurringView(plantId, reminderId, oldDueAt, type = "check"))
            as CareRepository.CompletionRecord.RecurringDone
        val expectedNext = CareSchedule.scheduledDueAt(record.completionAnchorAt, 7, 9, 0)

        val projected = careRepository.queue(record.completionAnchorAt, expectedNext + CareSchedule.DAY_MS)

        assertThat(db.plantReminderDao().getById(reminderId)?.createdAt).isEqualTo(record.completionAnchorAt)
        assertThat(projected.single { it.task.id is CareQueue.CareTask.TaskId.Recurring }.task.dueAt)
            .isEqualTo(expectedNext)
    }

    @Test
    fun editingReminderPreservesHistory_andMergeReturnsExistingId() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "watering",
                intervalDays = 7,
                enabled = true,
                createdAt = now - 7L * CareSchedule.DAY_MS,
            )
        )
        val eventId = db.plantReminderEventDao().insert(
            PlantReminderEventEntity(
                reminderId = reminderId,
                plantId = plantId,
                type = "watering",
                firedAt = now,
                action = "done",
                doneAt = now,
            )
        )
        val reminder = db.plantReminderDao().getById(reminderId)!!

        assertThat(plantRepository.saveReminderRow(reminder.copy(intervalDays = 10))).isEqualTo(reminderId)
        assertThat(db.plantReminderEventDao().getById(eventId)).isNotNull()
        assertThat(plantRepository.saveReminder(reminder.copy(intervalDays = 12))).isEqualTo(reminderId)
        assertThat(db.plantReminderEventDao().getById(eventId)).isNotNull()
    }

    @Test
    fun completingOneMultiRowPreset_doesNotReanchorItsSibling() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fern"))
        val originalAnchor = now - 14L * CareSchedule.DAY_MS
        val firstId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "mist",
                intervalDays = 7,
                enabled = true,
                createdAt = originalAnchor,
            )
        )
        val secondId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "mist",
                intervalDays = 10,
                enabled = true,
                createdAt = originalAnchor,
            )
        )

        careRepository.complete(recurringView(plantId, firstId, now - CareSchedule.DAY_MS, type = "mist"))

        val journal = db.plantJournalDao().getEntriesForPlant(plantId)
        val first = db.plantReminderDao().getById(firstId)!!
        val second = db.plantReminderDao().getById(secondId)!!
        assertThat(first.createdAt).isGreaterThan(originalAnchor)
        assertThat(second.createdAt).isEqualTo(originalAnchor)
        assertThat(plantRepository.reminderCadenceAnchor(second, journal)).isEqualTo(originalAnchor)
    }

    @Test
    fun completedOccurrence_remainsInOlderCalendarAfterLaterCareReanchorsReminder() = runBlocking {
        val now = System.currentTimeMillis()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val anchor = now - 70L * CareSchedule.DAY_MS
        val reminderId = db.plantReminderDao().upsert(
            PlantReminderEntity(
                plantId = plantId,
                type = "watering",
                intervalDays = 7,
                hour = 9,
                enabled = true,
                createdAt = anchor,
            )
        )
        val olderDueAt = CareSchedule.scheduledDueAt(anchor, 7, 9, 0)
        val laterDueAt = CareSchedule.scheduledDueAt(olderDueAt, 7, 9, 0)
        careRepository.complete(recurringView(plantId, reminderId, olderDueAt))
        careRepository.complete(recurringView(plantId, reminderId, laterDueAt))

        val olderDay = CareQueue.dayStartMs(CareQueue.epochDayOf(olderDueAt))
        val olderTasks = careRepository.day(olderDay)

        assertThat(olderTasks.map { it.task.id })
            .contains(CareQueue.CareTask.TaskId.Recurring(reminderId, CareQueue.epochDayOf(olderDueAt)))
        assertThat(olderTasks.single { it.task.dueAt == olderDueAt }.task.status)
            .isEqualTo(CareQueue.CareTask.Status.DONE)
    }

    @Test
    fun replayedCreateAttach_isOnePlantScanAssociationAndChecklistCommit() = runBlocking {
        val scanId = db.scanDao().insert(diseaseScan(plantId = null))
        val draft = CareRepository.NewPlantDraft(
            name = "Fig",
            species = "Rubber plant",
            scientificName = "Ficus elastica",
            commonName = "Rubber plant",
            roomId = null,
        )
        val stored = CareRepository.StoredChecklist(
            carePlan = CarePlanResponse(treatment = listOf("Remove damaged leaves.")),
            diseaseKey = "botrytis",
            diseaseLabelEn = "Grey mould",
            diseaseLabelRu = "Серая гниль",
        )

        val firstPlantId = careRepository.attachScanWithStoredChecklist(
            scanId = scanId,
            newPlant = draft,
            storedChecklist = stored,
        )
        val replayedPlantId = careRepository.attachScanWithStoredChecklist(
            scanId = scanId,
            newPlant = draft,
            storedChecklist = stored,
        )

        val attachedPlantId = firstPlantId!!
        assertThat(replayedPlantId).isEqualTo(attachedPlantId)
        assertThat(db.plantDao().getAllPlants()).hasSize(1)
        assertThat(db.scanDao().getScanById(scanId)?.plantId).isEqualTo(attachedPlantId)
        assertThat(db.treatmentPlanDao().getAllForPlant(attachedPlantId)).hasSize(1)
        assertThat(
            db.treatmentStepDao().getForPlan(
                db.treatmentPlanDao().getAllForPlant(attachedPlantId).single().id
            )
        )
            .hasSize(1)
    }

    @Test
    fun manualSpeciesCorrection_clearsCanonicalNamesWhileOrdinaryEditPreservesThem() = runBlocking {
        val plantId = db.plantDao().insert(
            PlantEntity(
                name = "Fig",
                species = "Rubber plant",
                scientificName = "Ficus elastica",
                commonName = "Rubber plant",
            )
        )
        val original = db.plantDao().getPlantById(plantId)!!
        plantRepository.savePlant("Fig renamed", original.species, original)
        val preserved = db.plantDao().getPlantById(plantId)!!
        assertThat(preserved.scientificName).isEqualTo("Ficus elastica")
        assertThat(preserved.commonName).isEqualTo("Rubber plant")

        plantRepository.savePlant(
            name = preserved.name,
            species = "Monstera",
            existing = preserved,
            canonicalSpeciesUpdate = PlantRepository.CanonicalSpeciesUpdate.REPLACE,
        )
        val corrected = db.plantDao().getPlantById(plantId)!!
        assertThat(corrected.species).isEqualTo("Monstera")
        assertThat(corrected.scientificName).isNull()
        assertThat(corrected.commonName).isNull()
    }

    @Test
    fun ensurePlan_preservesSourceScanAndOldScanNeverRecreatesAfterCompletion() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val firstScanId = db.scanDao().insert(diseaseScan(plantId))
        val secondScanId = db.scanDao().insert(diseaseScan(plantId))
        val plan = CarePlanResponse(treatment = listOf("Remove damaged leaves."))

        val firstPlanId = careRepository.ensureTreatmentPlanForScan(
            plantId,
            firstScanId,
            plan,
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )!!
        val skippedWhileSameDiseaseActive = careRepository.ensureTreatmentPlanForScan(
            plantId,
            secondScanId,
            plan,
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )

        assertThat(skippedWhileSameDiseaseActive).isNull()
        assertThat(db.treatmentPlanDao().getById(firstPlanId)?.scanId).isEqualTo(firstScanId)

        val firstPlan = db.treatmentPlanDao().getById(firstPlanId)!!
        db.treatmentPlanDao().update(firstPlan.copy(status = "completed", completedAt = System.currentTimeMillis()))
        assertThat(
            careRepository.ensureTreatmentPlanForScan(
                plantId,
                firstScanId,
                plan,
                "botrytis",
                "Grey mould",
                "Серая гниль",
            )
        ).isNull()
        assertThat(db.treatmentPlanDao().getAllForPlant(plantId)).hasSize(1)

        val secondPlanId = careRepository.ensureTreatmentPlanForScan(
            plantId,
            secondScanId,
            plan,
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )
        assertThat(secondPlanId).isNotNull()
        assertThat(db.treatmentPlanDao().getAllForPlant(plantId).map { it.scanId })
            .containsExactly(firstScanId, secondScanId)
    }

    @Test
    fun ensurePlan_upgradesUntouchedStoredChecklistWithServerCheckpoints() = runBlocking {
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        val scanId = db.scanDao().insert(diseaseScan(plantId))
        val stored = CarePlanResponse(treatment = listOf("Remove damaged leaves."))
        val planId = careRepository.ensureTreatmentPlanForScan(
            plantId,
            scanId,
            stored,
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )!!

        val enriched = CarePlanResponse(
            treatment = listOf("Remove damaged leaves."),
            checkpoints = listOf(CarePlanCheckpoint(day = 7, check = "Photograph new growth.")),
        )
        val enrichedId = careRepository.ensureTreatmentPlanForScan(
            plantId,
            scanId,
            enriched,
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )

        assertThat(enrichedId).isEqualTo(planId)
        assertThat(db.treatmentStepDao().getForPlan(planId).map { it.title })
            .containsExactly("Remove damaged leaves", "Photograph new growth")

        val firstCheckpointDueAt = db.treatmentStepDao().getForPlan(planId)
            .single { it.title == "Photograph new growth" }
            .dueAt
        val rescheduledId = careRepository.ensureTreatmentPlanForScan(
            plantId,
            scanId,
            enriched.copy(checkpoints = listOf(CarePlanCheckpoint(day = 9, check = "Photograph new growth."))),
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )
        val rescheduledSteps = db.treatmentStepDao().getForPlan(planId)

        assertThat(rescheduledId).isEqualTo(planId)
        assertThat(rescheduledSteps.single { it.title == "Photograph new growth" }.dueAt)
            .isGreaterThan(firstCheckpointDueAt)

        val completed = rescheduledSteps.first()
        db.treatmentStepDao().update(completed.copy(status = "done", doneAt = System.currentTimeMillis()))
        val rejectedAfterProgress = careRepository.ensureTreatmentPlanForScan(
            plantId,
            scanId,
            enriched.copy(
                checkpoints = listOf(
                    CarePlanCheckpoint(day = 9, check = "Photograph new growth."),
                    CarePlanCheckpoint(day = 14, check = "Check leaf undersides."),
                ),
            ),
            "botrytis",
            "Grey mould",
            "Серая гниль",
        )

        assertThat(rejectedAfterProgress).isNull()
        assertThat(db.treatmentStepDao().getForPlan(planId).map { it.title })
            .containsExactly("Remove damaged leaves", "Photograph new growth")
        assertThat(db.treatmentStepDao().getById(completed.id)?.status).isEqualTo("done")
    }

    private fun recurringView(
        plantId: Long,
        reminderId: Long,
        dueAt: Long,
        type: String = "watering",
    ) = CareRepository.CareTaskView(
        task = CareQueue.CareTask(
            id = CareQueue.CareTask.TaskId.Recurring(reminderId, CareQueue.epochDayOf(dueAt)),
            plantId = plantId,
            type = type,
            title = "Water",
            dueAt = dueAt,
            status = CareQueue.CareTask.Status.PENDING,
        ),
        plantName = "Fig",
        roomName = null,
        thumbnailSource = null,
    )

    private fun diseaseScan(plantId: Long?) = ScanEntity(
        imagePath = "file://scan.jpg",
        diseaseName = "Grey mould",
        diseaseNameRu = "Серая гниль",
        confidence = 0.9f,
        description = "",
        descriptionRu = "",
        treatment = "[]",
        treatmentRu = "[]",
        prevention = "[]",
        preventionRu = "[]",
        isHealthy = false,
        plantId = plantId,
    )
}
