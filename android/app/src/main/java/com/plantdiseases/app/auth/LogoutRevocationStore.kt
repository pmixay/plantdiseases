package com.plantdiseases.app.auth

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

data class PendingLogoutRevocation(val refreshToken: String)

interface LogoutRevocationStore {
    fun enqueue(refreshToken: String)

    fun next(): PendingLogoutRevocation?

    fun isPending(refreshToken: String): Boolean

    fun complete(refreshToken: String)

    fun recordFailure(refreshToken: String)
}

/** No persistence is used only by isolated unit-test/default construction. */
object NoOpLogoutRevocationStore : LogoutRevocationStore {
    override fun enqueue(refreshToken: String) = Unit

    override fun next(): PendingLogoutRevocation? = null

    override fun isPending(refreshToken: String): Boolean = false

    override fun complete(refreshToken: String) = Unit

    override fun recordFailure(refreshToken: String) = Unit
}

/**
 * Small encrypted, issuer-scoped retry queue. Entries are revocation proofs,
 * never active login credentials: AuthRepository cannot use this store for a
 * refresh. A queued proof is never dropped merely due to failed attempts, age,
 * or another logout: doing so can leave a remote session active until expiry,
 * and can let the same token silently restore if local vault erasure failed.
 * Only the account server's idempotent 204 removes a proof.
 */
class AndroidLogoutRevocationStore(
    context: Context,
    scope: AccountEndpointScope,
    private val nowEpochMillis: () -> Long = System::currentTimeMillis,
) : LogoutRevocationStore {
    private val store = ScopedEncryptedValueStore(context.applicationContext, scope, "logout")
    private val json = Json { ignoreUnknownKeys = true }

    @Synchronized
    override fun enqueue(refreshToken: String) {
        require(refreshToken.isNotBlank())
        val now = nowEpochMillis()
        val entries = readEntries()
            .filterNot { it.refreshToken == refreshToken }
            .plus(StoredRevocation(refreshToken, now, attempts = 0))
        write(entries)
    }

    @Synchronized
    override fun next(): PendingLogoutRevocation? =
        readEntries().firstOrNull()?.let { PendingLogoutRevocation(it.refreshToken) }

    @Synchronized
    override fun isPending(refreshToken: String): Boolean =
        readEntries().any { it.refreshToken == refreshToken }

    @Synchronized
    override fun complete(refreshToken: String) {
        write(readEntries().filterNot { it.refreshToken == refreshToken })
    }

    @Synchronized
    override fun recordFailure(refreshToken: String) {
        val entries = readEntries().map { entry ->
            if (entry.refreshToken == refreshToken) {
                entry.copy(attempts = (entry.attempts + 1).coerceAtMost(MAX_RECORDED_ATTEMPTS))
            } else {
                entry
            }
        }
        write(entries)
    }

    private fun readEntries(): List<StoredRevocation> {
        val encoded = store.read() ?: return emptyList()
        return json.decodeFromString<StoredQueue>(encoded).entries.also { entries ->
            require(entries.map { it.refreshToken }.distinct().size == entries.size)
            entries.forEach { entry ->
                require(entry.refreshToken.isNotBlank())
                require(entry.createdAtEpochMillis >= 0)
                require(entry.attempts in 0..MAX_RECORDED_ATTEMPTS)
            }
        }
    }

    private fun write(entries: List<StoredRevocation>) {
        if (entries.isEmpty()) {
            store.clear()
        } else {
            store.write(json.encodeToString(StoredQueue(entries)))
        }
    }

    @Serializable
    private data class StoredQueue(val entries: List<StoredRevocation>)

    @Serializable
    private data class StoredRevocation(
        val refreshToken: String,
        val createdAtEpochMillis: Long,
        val attempts: Int,
    )

    private companion object {
        const val MAX_RECORDED_ATTEMPTS = 1_000_000
    }
}
