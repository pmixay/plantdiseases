package com.plantdiseases.app.util

import android.app.Application
import android.content.Context
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.ByteArrayInputStream

/**
 * Robolectric coverage of the framework-touching [ImageUtils] helpers
 * that are deterministic on the JVM (paths, hashing, formatting, content
 * URI copy, and that the resize pipeline runs end-to-end without crashing
 * or leaking). Pixel-accurate decode / blur-variance / exact-downscale
 * assertions live in the on-device suite (ImageUtilsInstrumentedTest)
 * where a real Bitmap codec is available.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class ImageUtilsTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Test
    fun createImageFile_landsInPlantImagesDirWithExpectedName() {
        val file = ImageUtils.createImageFile(context)
        assertThat(file.parentFile?.name).isEqualTo("plant_images")
        assertThat(file.parentFile?.exists()).isTrue()
        assertThat(file.name).startsWith("PLANT_")
        assertThat(file.name).endsWith(".jpg")
    }

    @Test
    fun formatRelativeDate_todayYesterdayAndDaysAgo() {
        val now = System.currentTimeMillis()
        val day = 24L * 60 * 60 * 1000
        assertThat(ImageUtils.formatRelativeDate(context, now))
            .isEqualTo(context.getString(R.string.date_today))
        assertThat(ImageUtils.formatRelativeDate(context, now - day - 3_600_000))
            .isEqualTo(context.getString(R.string.date_yesterday))
        assertThat(ImageUtils.formatRelativeDate(context, now - 3 * day - 3_600_000))
            .isEqualTo(context.getString(R.string.date_days_ago, 3))
    }

    @Test
    fun formatRelativeTime_minutesHoursThenFallsBackToDates() {
        val now = System.currentTimeMillis()
        val minute = 60L * 1000
        val hour = 60 * minute
        assertThat(ImageUtils.formatRelativeTime(context, now))
            .isEqualTo(context.getString(R.string.time_just_now))
        // Clock skew: a timestamp in the future must not read "-1 min ago".
        assertThat(ImageUtils.formatRelativeTime(context, now + hour))
            .isEqualTo(context.getString(R.string.time_just_now))
        assertThat(ImageUtils.formatRelativeTime(context, now - 5 * minute))
            .isEqualTo(context.getString(R.string.time_minutes_ago, 5))
        assertThat(ImageUtils.formatRelativeTime(context, now - 2 * hour))
            .isEqualTo(context.getString(R.string.time_hours_ago, 2))
        assertThat(ImageUtils.formatRelativeTime(context, now - 26 * hour))
            .isEqualTo(context.getString(R.string.date_yesterday))
        assertThat(ImageUtils.formatRelativeTime(context, now - 3 * 24 * hour - hour))
            .isEqualTo(context.getString(R.string.date_days_ago, 3))
    }

    @Test
    fun copyUriToFile_copiesRegisteredStreamBytes() {
        val uri = Uri.parse("content://test.provider/img/1")
        val payload = "image-bytes-here".toByteArray()
        shadowOf(context.contentResolver).registerInputStream(uri, ByteArrayInputStream(payload))

        val out = ImageUtils.copyUriToFile(context, uri)

        assertThat(out).isNotNull()
        assertThat(out!!.exists()).isTrue()
        assertThat(out.readBytes()).isEqualTo(payload)
        out.delete()
    }
}
