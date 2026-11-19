package com.plantdiseases.app.data.repository

import android.content.Context
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.util.CommunityAvatarPrefs
import com.plantdiseases.app.util.ProfileAvatarStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Publishes the member's local profile photo ([ProfileAvatarStore]) to
 * Community, only after they agreed to.
 *
 * The photo is private by default: the Community tab asks once per photo
 * ([shouldAsk]) and the Profile toggle changes the answer ([setShown]).
 * [sync] reconciles the server with that choice: it uploads when the member
 * shows a photo the server does not hold yet (a new or changed photo is
 * detected by a content fingerprint), and withdraws it when they stop showing
 * it or remove the local photo. Uploads and withdrawals run in this
 * singleton's own scope, so leaving the screen does not cancel them.
 */
@Singleton
class CommunityAvatarSync @Inject constructor(
    @ApplicationContext context: Context,
    private val authRepository: AuthRepository,
    private val forum: ForumRepository,
    private val avatarStore: ProfileAvatarStore,
) {
    /** What a [sync] changed on the server. */
    enum class Outcome { NOTHING, PUBLISHED, WITHDRAWN, REJECTED, FAILED }

    /** What a [restore] changed on the device. */
    enum class RestoreOutcome { NOTHING, RESTORED, FAILED }

    private val prefs = CommunityAvatarPrefs(context)
    private val mutex = Mutex()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** Accounts whose server photo this process already restored or found absent; guarded by [mutex]. */
    private val restoreSettled = mutableSetOf<String>()

    /** The signed-in member's community actor id, or null for guests. */
    fun accountId(): String? =
        (authRepository.state.value as? AuthState.Authenticated)?.profile?.communityActorId

    /** Whether the signed-in member chose to show their photo (the Profile toggle). */
    fun isShown(): Boolean = accountId()?.let(prefs::choice) == true

    /** True when Community should ask: there is a local photo the member has not answered about. */
    suspend fun shouldAsk(): Boolean {
        val account = accountId() ?: return false
        val fingerprint = localFingerprint(account) ?: return false
        return Plan.shouldAsk(prefs.choice(account), prefs.askedFingerprint(account), fingerprint)
    }

    /** Record the member's answer for the current photo, then bring the server in line. */
    suspend fun setShown(show: Boolean): Outcome {
        val account = accountId() ?: return Outcome.NOTHING
        prefs.setChoice(account, show, localFingerprint(account))
        if (show) prefs.setRejected(account, null) // an explicit "show" retries a refused photo
        return sync()
    }

    /** Upload or withdraw so the server matches the member's choice and current photo. */
    suspend fun sync(): Outcome = scope.async { mutex.withLock { reconcile() } }.await()

    /**
     * Bring back the profile photo the member already shows in Community when
     * this device has none (the app was reinstalled, or this is a new phone):
     * the local copy and the "show" choice live only on the device, the
     * published photo on the server.
     *
     * Downloads the member's own public photo, stores it as the local profile
     * photo and records it as shown and already published, so the next [sync]
     * neither re-uploads it nor asks again. Does nothing for guests, when a
     * local photo exists, when the member chose not to show one on this
     * device, or when the server holds none. Asks the server at most once per
     * account per process unless a request failed.
     */
    suspend fun restore(): RestoreOutcome = scope.async { mutex.withLock { restoreLocked() } }.await()

    private suspend fun restoreLocked(): RestoreOutcome {
        val account = accountId() ?: return RestoreOutcome.NOTHING
        if (account in restoreSettled) return RestoreOutcome.NOTHING
        if (!Plan.shouldRestore(prefs.choice(account), hasLocalPhoto = avatarStore.current(account) != null)) {
            return RestoreOutcome.NOTHING
        }
        val me = forum.myAchievements().getOrElse { return RestoreOutcome.FAILED }
        val token = me.authorToken
        val version = me.avatarVersion
        if (token.isNullOrBlank() || version == null) {
            restoreSettled += account
            return RestoreOutcome.NOTHING
        }
        val bytes = forum.downloadAvatar(token, version).getOrElse { error ->
            if (error is ForumRepository.ForumException && error.status == HTTP_NOT_FOUND) {
                restoreSettled += account
                return RestoreOutcome.NOTHING
            }
            return RestoreOutcome.FAILED
        }
        // Signed out or switched accounts while downloading: not this account's photo to keep.
        if (accountId() != account) return RestoreOutcome.NOTHING
        val restored = avatarStore.restore(account, bytes).getOrElse {
            restoreSettled += account // an unreadable photo will not get better on retry
            return RestoreOutcome.FAILED
        }
        restoreSettled += account
        // A photo picked meanwhile wins and goes through the usual ask/sync path.
        restored ?: return RestoreOutcome.NOTHING
        val fingerprint = fingerprint(restored) ?: return RestoreOutcome.FAILED
        prefs.setChoice(account, true, fingerprint)
        prefs.setPublished(account, fingerprint)
        prefs.setRejected(account, null)
        return RestoreOutcome.RESTORED
    }

    private suspend fun reconcile(): Outcome {
        val account = accountId() ?: return Outcome.NOTHING
        val local = avatarStore.current(account)
        val fingerprint = local?.let(::fingerprint)
        val desired = Plan.desired(prefs.choice(account), fingerprint, prefs.rejectedFingerprint(account))
        val published = prefs.publishedFingerprint(account)
        return when {
            local != null && desired != null && desired != published -> publish(account, local, desired)
            desired == null && published != null -> withdraw(account)
            else -> Outcome.NOTHING
        }
    }

    private suspend fun publish(account: String, local: File, fingerprint: String): Outcome {
        val bytes = runCatching { local.readBytes() }.getOrNull() ?: return Outcome.FAILED
        return forum.uploadAvatar(bytes).fold(
            onSuccess = {
                prefs.setPublished(account, fingerprint)
                Outcome.PUBLISHED
            },
            onFailure = { error ->
                if (error is ForumRepository.ForumException && Plan.isRefusal(error.code)) {
                    prefs.setRejected(account, fingerprint)
                    Outcome.REJECTED
                } else {
                    Outcome.FAILED
                }
            },
        )
    }

    private suspend fun withdraw(account: String): Outcome = forum.deleteAvatar().fold(
        onSuccess = {
            prefs.setPublished(account, null)
            Outcome.WITHDRAWN
        },
        onFailure = { Outcome.FAILED },
    )

    private suspend fun localFingerprint(account: String): String? = withContext(Dispatchers.IO) {
        avatarStore.current(account)?.let(::fingerprint)
    }

    private fun fingerprint(file: File): String? = runCatching {
        MessageDigest.getInstance("SHA-256")
            .digest(file.readBytes())
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
    }.getOrNull()

    /** The decisions, free of Android and I/O so they can be unit-tested. */
    internal object Plan {
        /** Ask on first sight of a photo, and again for a new photo after "Not now". */
        fun shouldAsk(choice: Boolean?, askedFingerprint: String?, fingerprint: String?): Boolean =
            fingerprint != null && (choice == null || (choice == false && askedFingerprint != fingerprint))

        /** The photo the server should hold, or null for none. */
        fun desired(choice: Boolean?, fingerprint: String?, rejectedFingerprint: String?): String? =
            fingerprint?.takeIf { choice == true && it != rejectedFingerprint }

        /**
         * Restore the server's photo only when the device has none and the
         * member did not choose, on this device, to keep their photo private.
         */
        fun shouldRestore(choice: Boolean?, hasLocalPhoto: Boolean): Boolean = !hasLocalPhoto && choice != false

        /**
         * True only when the server rejected the photo itself (unreadable,
         * unsupported or banned). Anything else, such as a server that does
         * not have the avatar route yet (404), being muted, offline or over
         * quota, is retried on a later sync.
         */
        fun isRefusal(code: String): Boolean = code in PHOTO_REFUSALS

        private val PHOTO_REFUSALS = setOf("invalid_image", "unsupported_image_type", "image_not_allowed")
    }

    private companion object {
        const val HTTP_NOT_FOUND = 404
    }
}
