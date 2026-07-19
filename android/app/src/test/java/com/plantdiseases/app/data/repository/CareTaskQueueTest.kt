package com.plantdiseases.app.data.repository

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantReminderEntity
import com.plantdiseases.app.util.CareSchedule
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The Garden "Today" queue over real in-memory Room: which (plant, reminder)
 * pairs become due tasks, urgency ordering across plants, and the Done
 * round-trip — logCareAction writes the journal entry that becomes the new
 * schedule anchor and pulls the task back out of the queue.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CareTaskQueueTest {

    private lateinit var db: AppDatabase
    private lateinit var repo: PlantRepository

    private val now = System.currentTimeMillis()

    @Before
    fun setup() {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(ctx, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        repo = PlantRepository(
            db.plantDao(),
            db.plantJournalDao(),
            db.scanDao(),
            db.plantReminderDao(),
            db.plantReminderEventDao(),
            db.chatMessageDao(),
        )
    }

    @After
    fun teardown() {
        db.close()
    }

    private fun plant(name: String, createdDaysAgo: Int): PlantEntity = PlantEntity(
        name = name,
        createdAt = now - createdDaysAgo * CareSchedule.DAY_MS,
    )

    private fun reminder(
        plantId: Long,
        type: String = "watering",
        intervalDays: Int = 3,
        enabled: Boolean = true,
    ) = PlantReminderEntity(
        plantId = plantId,
        type = type,
        intervalDays = intervalDays,
        enabled = enabled,
    )

    @Test
    fun neverLoggedReminder_anchorsOnPlantCreation_andFallsDue() = runBlocking {
        val plantId = db.plantDao().insert(plant("Monstera", createdDaysAgo = 5))
        db.plantReminderDao().upsert(reminder(plantId, intervalDays = 3))

        val tasks = repo.getDueCareTasks(dueBefore = now)
        assertThat(tasks).hasSize(1)
        assertThat(tasks.single().plant.name).isEqualTo("Monstera")
        assertThat(tasks.single().reminder.type).isEqualTo("watering")
        // Due 3 days after creation = 2 days ago.
        assertThat(tasks.single().dueAt)
            .isEqualTo(tasks.single().plant.createdAt + 3 * CareSchedule.DAY_MS)
    }

    @Test
    fun disabledAndNonLoggableReminders_neverQueue() = runBlocking {
        val plantId = db.plantDao().insert(plant("Ficus", createdDaysAgo = 30))
        db.plantReminderDao().upsert(reminder(plantId, enabled = false))
        // "check" has no journal type a Done tap could anchor on — even
        // massively overdue it stays notification-only.
        db.plantReminderDao().upsert(reminder(plantId, type = "check", intervalDays = 1))

        assertThat(repo.getDueCareTasks(dueBefore = now)).isEmpty()
    }

    @Test
    fun notYetDueTask_staysOutOfTheQueue_untilTheBoundReachesIt() = runBlocking {
        val plantId = db.plantDao().insert(plant("Calathea", createdDaysAgo = 1))
        db.plantReminderDao().upsert(reminder(plantId, intervalDays = 3))

        // Due 2 days from now — outside today's bound…
        assertThat(repo.getDueCareTasks(dueBefore = now + CareSchedule.DAY_MS)).isEmpty()
        // …inside a bound that reaches past it.
        assertThat(repo.getDueCareTasks(dueBefore = now + 3 * CareSchedule.DAY_MS)).hasSize(1)
    }

    @Test
    fun queueOrdersMostUrgentFirst_acrossPlants() = runBlocking {
        val slightly = db.plantDao().insert(plant("Slightly", createdDaysAgo = 4))
        db.plantReminderDao().upsert(reminder(slightly, intervalDays = 3))
        val very = db.plantDao().insert(plant("Very", createdDaysAgo = 10))
        db.plantReminderDao().upsert(reminder(very, intervalDays = 3))

        val tasks = repo.getDueCareTasks(dueBefore = now)
        assertThat(tasks.map { it.plant.name }).containsExactly("Very", "Slightly").inOrder()
    }

    @Test
    fun logCareAction_journalsTheAction_andAdvancesTheTaskOutOfTheQueue() = runBlocking {
        val plantId = db.plantDao().insert(plant("Monstera", createdDaysAgo = 5))
        db.plantReminderDao().upsert(reminder(plantId, intervalDays = 3))
        assertThat(repo.getDueCareTasks(dueBefore = now)).hasSize(1)

        assertThat(repo.logCareAction(plantId, "watering")).isTrue()

        // The fresh journal entry is the new anchor → next due is ~3 days out.
        assertThat(repo.getDueCareTasks(dueBefore = now)).isEmpty()
        val entries = repo.getJournalEntries(plantId)
        assertThat(entries).hasSize(1)
        assertThat(entries.single().type)
            .isEqualTo(PlantRepository.JournalType.WATERING.value)
    }

    @Test
    fun logCareAction_refusesTypesWithoutAJournalMapping() = runBlocking {
        val plantId = db.plantDao().insert(plant("Ficus", createdDaysAgo = 1))
        assertThat(repo.logCareAction(plantId, "check")).isFalse()
        assertThat(repo.getJournalEntries(plantId)).isEmpty()
    }

    @Test
    fun queueAgreesWithTheGardenCardChip() = runBlocking {
        // The queue and PlantSummary.nextAction share the anchor rule — a
        // plant with a due task must also flag it on its card, and vice
        // versa, or the two surfaces would contradict each other.
        val plantId = db.plantDao().insert(plant("Monstera", createdDaysAgo = 5))
        db.plantReminderDao().upsert(reminder(plantId, intervalDays = 3))

        val task = repo.getDueCareTasks(dueBefore = now).single()
        val chip = repo.getPlants().single().nextAction!!
        assertThat(task.dueAt).isEqualTo(chip.dueAt)
        assertThat(task.reminder.type).isEqualTo(chip.type)
    }
}
