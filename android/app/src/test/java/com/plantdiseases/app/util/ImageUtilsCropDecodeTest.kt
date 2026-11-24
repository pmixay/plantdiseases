package com.plantdiseases.app.util

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import androidx.exifinterface.media.ExifInterface
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File
import java.io.FileOutputStream

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class ImageUtilsCropDecodeTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val context: Context = ApplicationProvider.getApplicationContext()

    private fun jpeg(width: Int, height: Int, orientation: Int? = null): Uri {
        val bmp = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        bmp.eraseColor(Color.GREEN)
        val file = File(tmp.root, "src_${width}x$height.jpg")
        FileOutputStream(file).use { bmp.compress(Bitmap.CompressFormat.JPEG, 90, it) }
        bmp.recycle()
        if (orientation != null) {
            ExifInterface(file.absolutePath).apply {
                setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
                saveAttributes()
            }
        }
        return Uri.fromFile(file)
    }

    @Test
    fun cropSampleSize_staysOneWithinTheCap() {
        assertThat(ImageUtils.cropSampleSize(2048, 1536)).isEqualTo(1)
        assertThat(ImageUtils.cropSampleSize(1000, 700)).isEqualTo(1)
    }

    @Test
    fun cropSampleSize_isLargestPowerOfTwoKeepingTheCap() {
        // 4000 -> 2000 would fall below 2048, so stays 1.
        assertThat(ImageUtils.cropSampleSize(4000, 3000)).isEqualTo(1)
        assertThat(ImageUtils.cropSampleSize(4096, 3072)).isEqualTo(2)
        assertThat(ImageUtils.cropSampleSize(8192, 6144)).isEqualTo(4)
        // Uses the long edge whichever axis it is.
        assertThat(ImageUtils.cropSampleSize(3000, 8192)).isEqualTo(4)
    }

    @Test
    fun decodeUpright_smallImageKeepsItsExactSize() {
        val bmp = ImageUtils.decodeUpright(context, jpeg(640, 480))!!

        assertThat(bmp.width).isEqualTo(640)
        assertThat(bmp.height).isEqualTo(480)
        assertThat(bmp.config).isEqualTo(Bitmap.Config.ARGB_8888)
    }

    @Test
    fun decodeUpright_largeImageIsCappedAtTheLongEdge() {
        val bmp = ImageUtils.decodeUpright(context, jpeg(5000, 3000))!!

        assertThat(maxOf(bmp.width, bmp.height)).isEqualTo(ImageUtils.CROP_MAX_EDGE)
        assertThat(bmp.width).isEqualTo(2048)
        assertThat(bmp.height).isEqualTo(1228)
    }

    @Test
    fun decodeUpright_bakesInRotationAfterTheCap() {
        val bmp = ImageUtils.decodeUpright(
            context,
            jpeg(5000, 3000, ExifInterface.ORIENTATION_ROTATE_90),
        )!!

        // Landscape source with a 90 degree tag comes out portrait.
        assertThat(bmp.width).isEqualTo(1228)
        assertThat(bmp.height).isEqualTo(2048)
    }

    @Test
    fun decodeUpright_smallRotatedImageSwapsDimensions() {
        val bmp = ImageUtils.decodeUpright(
            context,
            jpeg(640, 480, ExifInterface.ORIENTATION_ROTATE_270),
        )!!

        assertThat(bmp.width).isEqualTo(480)
        assertThat(bmp.height).isEqualTo(640)
    }

    @Test
    fun decodeUpright_missingSourceIsNull() {
        assertThat(ImageUtils.decodeUpright(context, Uri.fromFile(File(tmp.root, "missing.jpg")))).isNull()
    }
}
