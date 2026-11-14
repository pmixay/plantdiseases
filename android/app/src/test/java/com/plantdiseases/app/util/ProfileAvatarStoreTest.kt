package com.plantdiseases.app.util

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import java.io.File
import java.util.UUID
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class ProfileAvatarStoreTest {
    private val context: Context = ApplicationProvider.getApplicationContext()
    private val store = ProfileAvatarStore(context, DefaultUploadImageSanitizer)

    @Test
    fun selectedAvatarIsSanitizedAndScopedToOneAccount() = runTest {
        val source = createImage(1_200, 800)
        val accountId = "account-${UUID.randomUUID()}"
        try {
            val stored = store.replace(accountId, Uri.fromFile(source)).getOrThrow()
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(stored.absolutePath, bounds)

            assertThat(stored.canonicalPath)
                .startsWith(context.noBackupFilesDir.canonicalPath)
            assertThat(maxOf(bounds.outWidth, bounds.outHeight)).isAtMost(512)
            assertThat(store.current(accountId)).isEqualTo(stored)
            assertThat(store.current("another-${UUID.randomUUID()}")).isNull()
        } finally {
            store.clear(accountId)
            source.delete()
        }
    }

    @Test
    fun failedReplacementKeepsTheExistingAvatar() = runTest {
        val source = createImage(320, 240)
        val invalid = File.createTempFile("invalid_avatar_", ".image", context.cacheDir)
        val accountId = "account-${UUID.randomUUID()}"
        try {
            val stored = store.replace(accountId, Uri.fromFile(source)).getOrThrow()
            val original = stored.readBytes()

            assertThat(store.replace(accountId, Uri.fromFile(invalid)).isFailure).isTrue()
            assertThat(store.current(accountId)?.readBytes()).isEqualTo(original)
        } finally {
            store.clear(accountId)
            source.delete()
            invalid.delete()
        }
    }

    @Test
    fun editorWorkingCopyIsBoundedAndLivesOnlyInCache() = runTest {
        val source = createImage(2_400, 1_200)
        var staged: File? = null
        try {
            staged = store.stageForEditing(Uri.fromFile(source)).getOrThrow()
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(staged.absolutePath, bounds)

            assertThat(staged.canonicalPath).startsWith(context.cacheDir.canonicalPath)
            assertThat(maxOf(bounds.outWidth, bounds.outHeight)).isAtMost(2_048)
        } finally {
            staged?.delete()
            source.delete()
        }
    }

    private fun createImage(width: Int, height: Int): File {
        val source = File.createTempFile("avatar_source_", ".png", context.cacheDir)
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        try {
            bitmap.eraseColor(Color.rgb(34, 139, 74))
            source.outputStream().use { output ->
                check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, output))
            }
        } finally {
            bitmap.recycle()
        }
        return source
    }
}
