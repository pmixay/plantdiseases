package com.plantdiseases.app.data.local

import android.app.Application
import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.repository.NotificationRepository
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Notification-centre storage over the real Room DAO (in-memory): the
 * unread count is what drives the Home bell's badge, so it gets an
 * explicit read/mark/read-again assertion rather than trusting the SQL by
 * eye. Retention and per-row delete are covered too, since both back
 * user-visible actions (30-day sweep, swipe-to-dismiss).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class NotificationCenterTest {

    private lateinit var db: AppDatabase
    private lateinit var repo: NotificationRepository

    @Before
    fun setup() {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(ctx, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        repo = NotificationRepository(db.notificationDao())
    }

    @After
    fun teardown() {
        db.close()
    }

    @Test
    fun unreadCount_dropsAsRowsAreRead() = runBlocking {
        repo.record("community", "Reply", "Someone answered", "community:abc")
        repo.record("reminder", "Water", "Time to water", "plant:7")
        assertThat(repo.getUnreadCount()).isEqualTo(2)

        val newest = repo.getAll().first()
        repo.markRead(newest.id)
        assertThat(repo.getUnreadCount()).isEqualTo(1)

        repo.markAllRead()
        assertThat(repo.getUnreadCount()).isEqualTo(0)
        // Marking read must not delete anything — the list still shows them.
        assertThat(repo.getAll()).hasSize(2)
    }

    @Test
    fun getAll_isNewestFirst() = runBlocking {
        repo.record("community", "older", "b", null)
        Thread.sleep(5)
        repo.record("reminder", "newer", "b", null)
        assertThat(repo.getAll().map { it.title }).containsExactly("newer", "older").inOrder()
    }

    @Test
    fun delete_removesOnlyThatRow(): Unit = runBlocking {
        repo.record("community", "keep", "b", null)
        repo.record("garden", "drop", "b", null)
        val drop = repo.getAll().first { it.title == "drop" }
        repo.delete(drop.id)
        assertThat(repo.getAll().map { it.title }).containsExactly("keep")
    }

    @Test
    fun deleteOlderThan_sweepsOnlyStaleRows(): Unit = runBlocking {
        repo.record("community", "fresh", "b", null)
        // Backdate one row past the retention window the app sweeps on launch.
        db.notificationDao().insert(
            NotificationEntity(
                type = "reminder",
                title = "stale",
                body = "b",
                createdAt = System.currentTimeMillis() - 31L * 24 * 3_600_000,
            ),
        )
        repo.deleteOlderThan(System.currentTimeMillis() - 30L * 24 * 3_600_000)
        assertThat(repo.getAll().map { it.title }).containsExactly("fresh")
    }
}
