package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.net.Uri
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Turns a picked photo into upload-ready bytes: decoded, oriented, bounded and
 * re-encoded as a fresh JPEG, so no location or camera metadata leaves the
 * phone. The server re-encodes again on its side.
 */
object ForumPhotos {
    private const val MAX_EDGE = 1_600
    private const val QUALITY = 85

    suspend fun prepare(context: Context, uri: Uri): ByteArray? = withContext(Dispatchers.IO) {
        val temp = File.createTempFile("forum-photo", ".img", context.cacheDir)
        try {
            val copied = context.contentResolver.openInputStream(uri)?.use { input ->
                temp.outputStream().use { output -> input.copyTo(output) }
                true
            } ?: false
            if (!copied) return@withContext null
            DefaultUploadImageSanitizer.sanitize(temp, MAX_EDGE, QUALITY).bytes
        } catch (_: Exception) {
            null
        } finally {
            temp.delete()
        }
    }
}
