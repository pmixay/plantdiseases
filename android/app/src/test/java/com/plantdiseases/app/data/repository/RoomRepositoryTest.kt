package com.plantdiseases.app.data.repository

import android.app.Application
import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.local.PlantEntity
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
@Config(sdk = [33], application = Application::class)
class RoomRepositoryTest {

    private lateinit var db: AppDatabase
    private lateinit var repository: RoomRepository

    @Before
    fun setup() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        repository = RoomRepository(db, db.roomDao(), db.plantDao(), db.scanDao())
    }

    @After
    fun teardown() {
        db.close()
    }

    @Test
    fun concurrentCaseVariantCreates_commitOneUnicodeRoom() = runBlocking {
        val results = coroutineScope {
            val first = async { repository.createRoom("Кухня", "kitchen") }
            val second = async { repository.createRoom("кухня", "kitchen") }
            listOf(first.await(), second.await())
        }

        assertThat(results.filterIsInstance<RoomRepository.RoomNameResult.Success>()).hasSize(1)
        assertThat(results.filterIsInstance<RoomRepository.RoomNameResult.NameTaken>()).hasSize(1)
        assertThat(db.roomDao().getAll()).hasSize(1)
    }

    @Test
    fun deleteRoom_unassignsAllPlantsBeforeDeletingRoom() = runBlocking {
        val roomId = (repository.createRoom("Office", "office") as RoomRepository.RoomNameResult.Success).roomId
        val firstPlantId = db.plantDao().insert(PlantEntity(name = "Fig", roomId = roomId))
        val secondPlantId = db.plantDao().insert(PlantEntity(name = "Fern", roomId = roomId))

        repository.deleteRoom(roomId)

        assertThat(db.roomDao().getById(roomId)).isNull()
        assertThat(db.plantDao().getPlantById(firstPlantId)?.roomId).isNull()
        assertThat(db.plantDao().getPlantById(secondPlantId)?.roomId).isNull()
    }
}
