package com.plantdiseases.app.util

import android.content.Context
import android.net.Uri
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Stores a user-selected profile picture in private, non-backed-up app storage.
 *
 * The provider picture remains the fallback. A selected image is keyed by the
 * account's opaque community actor ID so signing into another account can never
 * reveal the previous account's local picture. Imports are bounded, decoded,
 * oriented and freshly encoded through [UploadImageSanitizer], which strips
 * metadata before anything becomes durable.
 */
@Singleton
class ProfileAvatarStore @Inject constructor(
    @ApplicationContext private val context: Context,
    private val imageSanitizer: UploadImageSanitizer,
) {
    private val mutationMutex = Mutex()

    fun current(accountId: String): File? = runCatching {
        avatarFile(accountId).takeIf { file ->
            file.isFile && file.length() in 1..MAX_STORED_BYTES
        }
    }.getOrNull()

    suspend fun replace(accountId: String, source: Uri): Result<File> = try {
        val avatar = withContext(Dispatchers.IO) {
            mutationMutex.withLock { replaceLocked(accountId, source) }
        }
        Result.success(avatar)
    } catch (failure: CancellationException) {
        throw failure
    } catch (failure: Exception) {
        Result.failure(failure)
    } catch (failure: OutOfMemoryError) {
        Result.failure(ImageUtils.ImageDecodeException("Avatar is too large", failure))
    }

    /** Create a bounded, metadata-free working copy for the circular editor. */
    suspend fun stageForEditing(source: Uri): Result<File> = try {
        val staged = withContext(Dispatchers.IO) {
            mutationMutex.withLock { stageForEditingLocked(source) }
        }
        Result.success(staged)
    } catch (failure: CancellationException) {
        throw failure
    } catch (failure: Exception) {
        Result.failure(failure)
    } catch (failure: OutOfMemoryError) {
        Result.failure(ImageUtils.ImageDecodeException("Avatar is too large", failure))
    }

    /**
     * Store a photo downloaded from the member's own Community profile as the
     * local profile photo (after a reinstall, the server holds the only copy).
     * The bytes go through the same sanitizer as a picked photo. A local photo
     * that already exists wins: the result is then `null` and nothing changes.
     */
    suspend fun restore(accountId: String, jpeg: ByteArray): Result<File?> = try {
        val avatar = withContext(Dispatchers.IO) {
            mutationMutex.withLock {
                if (current(accountId) != null) null else restoreLocked(accountId, jpeg)
            }
        }
        Result.success(avatar)
    } catch (failure: CancellationException) {
        throw failure
    } catch (failure: Exception) {
        Result.failure(failure)
    } catch (failure: OutOfMemoryError) {
        Result.failure(ImageUtils.ImageDecodeException("Avatar is too large", failure))
    }

    suspend fun clear(accountId: String) {
        withContext(Dispatchers.IO) {
            mutationMutex.withLock {
                runCatching { avatarFile(accountId).delete() }
            }
        }
    }

    private fun replaceLocked(accountId: String, source: Uri): File {
        val sanitized = sanitizeSource(
            source,
            maxDimension = AVATAR_MAX_DIMENSION,
            quality = AVATAR_JPEG_QUALITY,
        )
        return install(accountId, sanitized)
    }

    private fun restoreLocked(accountId: String, jpeg: ByteArray): File {
        if (jpeg.isEmpty() || jpeg.size > MAX_SOURCE_BYTES) {
            throw ImageUtils.ImageDecodeException("Avatar source is invalid")
        }
        val sourceCopy = File.createTempFile("profile_avatar_source_", ".image", context.cacheDir)
        val sanitized = try {
            writeSynced(sourceCopy, jpeg)
            imageSanitizer.sanitize(sourceCopy, maxDimension = AVATAR_MAX_DIMENSION, quality = AVATAR_JPEG_QUALITY)
        } finally {
            sourceCopy.delete()
        }
        return install(accountId, sanitized)
    }

    /** Atomically make [sanitized] the account's profile photo. */
    private fun install(accountId: String, sanitized: SanitizedImage): File {
        if (sanitized.bytes.isEmpty() || sanitized.bytes.size > MAX_STORED_BYTES) {
            throw ImageUtils.ImageDecodeException("Avatar output is invalid")
        }
        val staged = File.createTempFile("profile_avatar_", ".tmp", avatarDirectory())
        try {
            writeSynced(staged, sanitized.bytes)
            val target = avatarFile(accountId)
            try {
                Files.move(
                    staged.toPath(),
                    target.toPath(),
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING,
                )
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(
                    staged.toPath(),
                    target.toPath(),
                    StandardCopyOption.REPLACE_EXISTING,
                )
            }
            return target
        } finally {
            staged.delete()
        }
    }

    private fun stageForEditingLocked(source: Uri): File {
        val staged = File.createTempFile("profile_avatar_editor_", ".jpg", context.cacheDir)
        var completed = false
        try {
            val sanitized = sanitizeSource(
                source,
                maxDimension = EDITOR_MAX_DIMENSION,
                quality = EDITOR_JPEG_QUALITY,
            )
            writeSynced(staged, sanitized.bytes)
            completed = true
            return staged
        } finally {
            if (!completed) staged.delete()
        }
    }

    private fun sanitizeSource(source: Uri, maxDimension: Int, quality: Int): SanitizedImage {
        val sourceCopy = File.createTempFile("profile_avatar_source_", ".image", context.cacheDir)
        return try {
            copyBounded(source, sourceCopy)
            imageSanitizer.sanitize(sourceCopy, maxDimension = maxDimension, quality = quality)
        } finally {
            sourceCopy.delete()
        }
    }

    private fun writeSynced(target: File, bytes: ByteArray) {
        FileOutputStream(target).use { output ->
            output.write(bytes)
            output.fd.sync()
        }
    }

    private fun copyBounded(source: Uri, target: File) {
        val input = openSource(source)
        input.use { stream ->
            FileOutputStream(target).use { output ->
                val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                var copied = 0L
                while (true) {
                    val read = stream.read(buffer)
                    if (read < 0) break
                    copied += read
                    if (copied > MAX_SOURCE_BYTES) {
                        throw ImageUtils.ImageDecodeException("Avatar source is too large")
                    }
                    output.write(buffer, 0, read)
                }
                if (copied == 0L) {
                    throw ImageUtils.ImageDecodeException("Avatar source is empty")
                }
            }
        }
    }

    private fun openSource(source: Uri) = context.contentResolver.openInputStream(source)
        ?: throw IOException("Avatar source is unavailable")

    private fun avatarDirectory(): File = File(context.noBackupFilesDir, DIRECTORY_NAME).also {
        if (!it.isDirectory && !it.mkdirs()) {
            throw IOException("Avatar directory is unavailable")
        }
    }

    private fun avatarFile(accountId: String): File {
        require(accountId.isNotBlank() && accountId.length <= MAX_ACCOUNT_ID_LENGTH)
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(accountId.toByteArray(Charsets.UTF_8))
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        return File(avatarDirectory(), "$digest.jpg")
    }

    companion object {
        private const val DIRECTORY_NAME = "profile_avatars"
        private const val MAX_ACCOUNT_ID_LENGTH = 512
        private const val MAX_SOURCE_BYTES = 16L * 1_024 * 1_024
        private const val MAX_STORED_BYTES = 4L * 1_024 * 1_024
        private const val AVATAR_MAX_DIMENSION = 512
        private const val AVATAR_JPEG_QUALITY = 90
        private const val EDITOR_MAX_DIMENSION = 2_048
        private const val EDITOR_JPEG_QUALITY = 92
    }
}
