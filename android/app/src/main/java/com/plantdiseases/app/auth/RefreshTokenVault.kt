package com.plantdiseases.app.auth

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

data class PreparedRefresh(
    val refreshToken: String,
    val rotationId: String,
)

interface RefreshTokenVault {
    fun read(): String?

    /** Persists one idempotency identity before its refresh request is sent. */
    fun prepareRefresh(): PreparedRefresh?

    fun write(token: String)

    fun clear()
}

class CredentialUnreadableException(
    cause: Throwable? = null,
) : Exception("Stored account credential is unreadable", cause)

/**
 * Stores only an AES-GCM ciphertext under a key and preferences namespace
 * derived from the normalized account issuer. The issuer is also authenticated
 * as GCM additional data, so copying a ciphertext between namespaces fails
 * closed even if the preferences were tampered with.
 */
class AndroidKeystoreRefreshTokenVault(
    context: Context,
    scope: AccountEndpointScope,
) : RefreshTokenVault {
    private val applicationContext = context.applicationContext
    private val store = ScopedEncryptedValueStore(applicationContext, scope, "refresh")
    private val json = Json { ignoreUnknownKeys = true }

    init {
        // Beta builds used one package-global token/key. There is no safe way
        // to infer which backend issued it, so deliberately destroy it rather
        // than migrating it into the configured environment.
        check(
            applicationContext.getSharedPreferences(LEGACY_PREFS, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .commit(),
        ) { "Unable to purge unscoped beta credential" }
        runCatching { androidKeyStore().deleteEntry(LEGACY_KEY_ALIAS) }
    }

    @Synchronized
    override fun read(): String? = readStored()?.refreshToken

    @Synchronized
    override fun prepareRefresh(): PreparedRefresh? {
        val credential = readStored() ?: return null
        val rotationId = credential.pendingRotationId ?: UUID.randomUUID().toString().also { id ->
            writeStored(credential.copy(pendingRotationId = id))
        }
        return PreparedRefresh(credential.refreshToken, rotationId)
    }

    @Synchronized
    override fun write(token: String) = writeStored(StoredRefreshCredential(token))

    @Synchronized
    override fun clear() = store.clear()

    private fun readStored(): StoredRefreshCredential? {
        val encoded = store.read() ?: return null
        if (!encoded.startsWith(STORAGE_PREFIX)) {
            // Scoped v2 beta credentials were stored as a raw token. They are
            // safe to migrate because the namespace is already issuer-bound.
            return StoredRefreshCredential(encoded)
        }
        return try {
            json.decodeFromString<StoredRefreshCredential>(encoded.removePrefix(STORAGE_PREFIX))
                .also { credential ->
                    require(credential.refreshToken.isNotBlank())
                    credential.pendingRotationId?.let(UUID::fromString)
                }
        } catch (failure: Exception) {
            runCatching { store.clear() }
            throw CredentialUnreadableException(failure)
        }
    }

    private fun writeStored(credential: StoredRefreshCredential) {
        require(credential.refreshToken.isNotBlank())
        store.write(STORAGE_PREFIX + json.encodeToString(credential))
    }

    private companion object {
        const val LEGACY_PREFS = "account_refresh_v1"
        const val LEGACY_KEY_ALIAS = "plantdiseases.account.refresh.v1"
        const val STORAGE_PREFIX = "plantdiseases-refresh-v1\n"
    }
}

@Serializable
private data class StoredRefreshCredential(
    val refreshToken: String,
    val pendingRotationId: String? = null,
)

/** Shared primitive for active and pending-revocation credential stores. */
internal class ScopedEncryptedValueStore(
    context: Context,
    private val scope: AccountEndpointScope,
    namespace: String,
) {
    private val prefs = context.applicationContext.getSharedPreferences(
        "account_${namespace}_v2_${scope.storageKey}",
        Context.MODE_PRIVATE,
    )
    private val keyAlias = "plantdiseases.account.$namespace.v2.${scope.storageKey}"

    fun read(): String? {
        val encoded = try {
            prefs.getString(KEY_CIPHERTEXT, null)
        } catch (failure: Exception) {
            unreadable(failure)
        } ?: return null
        val storedIssuer = try {
            prefs.getString(KEY_ISSUER, null)
        } catch (failure: Exception) {
            unreadable(failure)
        }
        if (storedIssuer != scope.issuer) {
            unreadable(
                IllegalStateException("Credential issuer binding is invalid"),
            )
        }
        return try {
            val packed = Base64.decode(encoded, Base64.NO_WRAP)
            require(packed.size > IV_BYTES)
            val key = androidKeyStore().getKey(keyAlias, null) as? SecretKey
                ?: error("Credential key is missing")
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                key,
                GCMParameterSpec(TAG_BITS, packed.copyOfRange(0, IV_BYTES)),
            )
            cipher.updateAAD(scope.issuer.toByteArray(Charsets.UTF_8))
            cipher.doFinal(packed.copyOfRange(IV_BYTES, packed.size)).toString(Charsets.UTF_8)
        } catch (failure: Exception) {
            unreadable(failure)
        }
    }

    fun write(value: String) {
        require(value.isNotBlank())
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        cipher.updateAAD(scope.issuer.toByteArray(Charsets.UTF_8))
        val packed = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        check(
            prefs.edit()
                .putString(KEY_ISSUER, scope.issuer)
                .putString(KEY_CIPHERTEXT, Base64.encodeToString(packed, Base64.NO_WRAP))
                .commit(),
        ) { "Unable to persist account credential" }
    }

    fun clear() {
        // Clear both halves independently. If the preferences commit fails we
        // must still destroy the encryption key; otherwise logout can appear
        // complete in this process while the intact ciphertext + key silently
        // restore the account after a restart. Either successful operation is
        // sufficient cryptographic erasure. Report failure only when neither
        // storage layer could make the credential unrecoverable.
        val ciphertextCleared = prefs.edit().clear().commit()
        val keyCleared = runCatching { androidKeyStore().deleteEntry(keyAlias) }.isSuccess
        check(ciphertextCleared || keyCleared) { "Unable to clear account credential" }
    }

    private fun unreadable(failure: Exception): Nothing {
        runCatching { clear() }
        throw CredentialUnreadableException(failure)
    }

    private fun getOrCreateKey(): SecretKey {
        val store = androidKeyStore()
        (store.getKey(keyAlias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE).run {
            init(
                KeyGenParameterSpec.Builder(
                    keyAlias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true)
                    .build(),
            )
            generateKey()
        }
    }

    private companion object {
        const val KEY_ISSUER = "issuer"
        const val KEY_CIPHERTEXT = "ciphertext"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
    }
}

private fun androidKeyStore(): KeyStore =
    KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
