package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.NotificationDao
import com.plantdiseases.app.data.local.NotificationEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Thin wrapper over [NotificationDao] for the notification centre — see
 *  NotificationEntity's kdoc in Database.kt. */
class NotificationRepository(
    private val notificationDao: NotificationDao,
) {
    suspend fun getAll(): List<NotificationEntity> =
        withContext(Dispatchers.IO) { notificationDao.getAll() }

    suspend fun getUnreadCount(): Int =
        withContext(Dispatchers.IO) { notificationDao.getUnreadCount() }

    suspend fun record(type: String, title: String, body: String, deeplink: String?): Long =
        withContext(Dispatchers.IO) {
            notificationDao.insert(
                NotificationEntity(
                    type = type,
                    title = title,
                    body = body,
                    deeplink = deeplink,
                    createdAt = System.currentTimeMillis(),
                ),
            )
        }

    /** Stores a row built elsewhere (e.g. a Community event with its own time). */
    suspend fun insert(entity: NotificationEntity): Long =
        withContext(Dispatchers.IO) { notificationDao.insert(entity) }

    suspend fun markRead(id: Long) = withContext(Dispatchers.IO) {
        notificationDao.markRead(id, System.currentTimeMillis())
    }

    suspend fun markAllRead() = withContext(Dispatchers.IO) {
        notificationDao.markAllRead(System.currentTimeMillis())
    }

    suspend fun clearAll() = withContext(Dispatchers.IO) { notificationDao.clearAll() }

    suspend fun delete(id: Long) = withContext(Dispatchers.IO) { notificationDao.deleteById(id) }

    suspend fun deleteOlderThan(cutoffMillis: Long) = withContext(Dispatchers.IO) {
        notificationDao.deleteOlderThan(cutoffMillis)
    }
}
