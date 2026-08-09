package com.plantdiseases.app.auth

import android.content.Context
import com.plantdiseases.app.data.account.AuthProvider
import java.util.UUID
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** Durable identity and bearer for one two-phase signup operation. */
data class PendingSignupOperation(
    val provider: AuthProvider,
    val pendingSignupToken: String,
    val completionId: String,
    val installationId: String,
    val expiresInSeconds: Int,
    /** Set and persisted before the first completion request is sent. */
    val nickname: String? = null,
    /** Persisted after refresh storage and before confirmation can be sent. */
    val confirmationAttempted: Boolean = false,
    /** Remote cancel/logout was proven; only local marker-last cleanup remains. */
    val remoteTerminationConfirmed: Boolean = false,
)

interface SignupCompletionStore {
    fun read(): PendingSignupOperation?

    fun write(operation: PendingSignupOperation)

    fun clear()
}

/** Test/default store; production dependency injection always supplies Keystore storage. */
class InMemorySignupCompletionStore : SignupCompletionStore {
    private var operation: PendingSignupOperation? = null

    @Synchronized
    override fun read(): PendingSignupOperation? = operation

    @Synchronized
    override fun write(operation: PendingSignupOperation) {
        validate(operation)
        this.operation = operation
    }

    @Synchronized
    override fun clear() {
        operation = null
    }
}

/** Issuer-scoped AES-GCM storage for the provider-bound pending bearer. */
class AndroidKeystoreSignupCompletionStore(
    context: Context,
    scope: AccountEndpointScope,
) : SignupCompletionStore {
    private val encrypted = ScopedEncryptedValueStore(context.applicationContext, scope, "signup")
    private val json = Json { ignoreUnknownKeys = false }

    @Synchronized
    override fun read(): PendingSignupOperation? {
        val encoded = encrypted.read() ?: return null
        return try {
            val stored = json.decodeFromString<StoredSignupOperation>(encoded)
            stored.toOperation().also(::validate)
        } catch (failure: Exception) {
            runCatching { encrypted.clear() }
            throw CredentialUnreadableException(failure)
        }
    }

    @Synchronized
    override fun write(operation: PendingSignupOperation) {
        validate(operation)
        encrypted.write(json.encodeToString(StoredSignupOperation.from(operation)))
    }

    @Synchronized
    override fun clear() = encrypted.clear()
}

@Serializable
private data class StoredSignupOperation(
    val provider: String,
    val pendingSignupToken: String,
    val completionId: String,
    val installationId: String,
    val expiresInSeconds: Int,
    val nickname: String? = null,
    val confirmationAttempted: Boolean = false,
    val remoteTerminationConfirmed: Boolean = false,
) {
    fun toOperation() = PendingSignupOperation(
        provider = AuthProvider.entries.firstOrNull { it.wireName == provider }
            ?: error("Unknown signup provider"),
        pendingSignupToken = pendingSignupToken,
        completionId = completionId,
        installationId = installationId,
        expiresInSeconds = expiresInSeconds,
        nickname = nickname,
        confirmationAttempted = confirmationAttempted,
        remoteTerminationConfirmed = remoteTerminationConfirmed,
    )

    companion object {
        fun from(operation: PendingSignupOperation) = StoredSignupOperation(
            provider = operation.provider.wireName,
            pendingSignupToken = operation.pendingSignupToken,
            completionId = operation.completionId,
            installationId = operation.installationId,
            expiresInSeconds = operation.expiresInSeconds,
            nickname = operation.nickname,
            confirmationAttempted = operation.confirmationAttempted,
            remoteTerminationConfirmed = operation.remoteTerminationConfirmed,
        )
    }
}

private fun validate(operation: PendingSignupOperation) {
    require(operation.pendingSignupToken.isNotBlank())
    require(operation.pendingSignupToken.length <= 256)
    UUID.fromString(operation.completionId)
    UUID.fromString(operation.installationId)
    require(operation.expiresInSeconds > 0)
    operation.nickname?.let {
        require(it.isNotBlank())
        require(it.length <= 64)
    }
}
