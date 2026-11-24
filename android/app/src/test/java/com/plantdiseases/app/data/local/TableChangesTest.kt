package com.plantdiseases.app.data.local

import android.app.Application
import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The garden's live signal: one emission on collect, one per committed write
 *  to a garden table, none for a table the garden does not read. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class TableChangesTest {

    private lateinit var db: AppDatabase

    @Before
    fun setup() {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(ctx, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        // Open the database before the flow is collected. Otherwise the first
        // emission waits for the open, the schema and Room's tracker set-up, and
        // on a loaded CI runner (parallel forks) that alone can pass the timeout.
        check(db.openHelper.writableDatabase.isOpen)
    }

    @After
    fun teardown() {
        db.close()
    }

    @Test
    fun gardenChangesFollowGardenWritesOnly() = runBlocking {
        val seen = Channel<Unit>(Channel.UNLIMITED)
        val collector = launch(Dispatchers.Default) { db.gardenChanges().collect { seen.send(it) } }
        suspend fun next() = withTimeout(TIMEOUT_MS) { seen.receive() }

        next() // the initial state
        // Like the garden, read after each emission: Room starts watching the
        // tables when a query runs, so a write after this read is observed.
        db.plantDao().getAllPlants()
        val plantId = db.plantDao().insert(PlantEntity(name = "Fig"))
        next()
        db.roomDao().insert(RoomEntity(name = "Kitchen"))
        next()
        db.plantJournalDao().insert(PlantJournalEntity(plantId = plantId, type = "water"))
        next()

        // Notifications are not a garden table: no re-read.
        db.notificationDao().insert(
            NotificationEntity(type = "x", title = "t", body = "b", deeplink = null, createdAt = 1L),
        )
        assertThat(withTimeoutOrNull(QUIET_MS) { seen.receive() }).isNull()
        collector.cancel()
    }

    private companion object {
        // Only reached when an emission is missing; generous so a slow runner
        // is not read as a missed write.
        const val TIMEOUT_MS = 15_000L
        const val QUIET_MS = 500L
    }
}
