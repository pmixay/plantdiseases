package com.plantdiseases.app.pipeline

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.plantdiseases.app.util.ImageUtils
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.io.FileOutputStream

/**
 * On-device coverage of the real image-preparation pipeline with an
 * actual Bitmap codec: downscale-for-upload bounds, the blur (Laplacian
 * variance) gate, and the corrupt-file contract. Requires an emulator
 * or device (./gradlew connectedDebugAndroidTest).
 */
@RunWith(AndroidJUnit4::class)
class ImageUtilsInstrumentedTest {

    private val ctx: Context get() = ApplicationProvider.getApplicationContext()

    @Test
    fun prepareImageForUpload_downscalesLargeImageToMaxEdge() {
        val src = saveJpeg(solid(2000, 1500, Color.rgb(40, 160, 60)), "src_large.jpg")
        val out = ImageUtils.prepareImageForUpload(ctx, src.absolutePath, maxSize = 1024)

        assertTrue("output exists", out.exists())
        assertTrue("output non-empty", out.length() > 0)
        val bounds = decodeBounds(out)
        assertEquals(1024, maxOf(bounds.first, bounds.second))
        // 1500 * (1024/2000) == 768
        assertEquals(768, minOf(bounds.first, bounds.second))

        src.delete()
        out.delete()
    }

    @Test
    fun prepareImageForUpload_doesNotUpscaleSmallImage() {
        val src = saveJpeg(solid(240, 180, Color.rgb(10, 120, 10)), "src_small.jpg")
        val out = ImageUtils.prepareImageForUpload(ctx, src.absolutePath, maxSize = 1024)
        val bounds = decodeBounds(out)
        assertEquals(240, maxOf(bounds.first, bounds.second))
        src.delete()
        out.delete()
    }

    @Test
    fun prepareImageForUpload_throwsOnCorruptFile() {
        val bad = File(ctx.cacheDir, "corrupt.jpg").apply { writeText("definitely not an image") }
        assertThrows(ImageUtils.ImageDecodeException::class.java) {
            ImageUtils.prepareImageForUpload(ctx, bad.absolutePath)
        }
        bad.delete()
    }

    @Test
    fun computeBlurScore_sharpImageScoresHigherThanFlat() {
        val flat = savePng(solid(256, 256, Color.WHITE), "flat.png")
        val sharp = savePng(checkerboard(256, 16), "sharp.png")

        val flatScore = ImageUtils.computeBlurScore(flat.absolutePath)
        val sharpScore = ImageUtils.computeBlurScore(sharp.absolutePath)

        assertTrue("flat should be near-zero variance, was $flatScore", flatScore < 50.0)
        assertTrue("sharp ($sharpScore) should exceed flat ($flatScore)", sharpScore > flatScore)
        flat.delete()
        sharp.delete()
    }

    @Test
    fun computeBlurScore_missingFileAssumedSharp() {
        assertEquals(Double.MAX_VALUE, ImageUtils.computeBlurScore("/no/such/file.jpg"), 0.0)
    }

    // ─── helpers ─────────────────────────────────────────────────────────

    private fun solid(w: Int, h: Int, color: Int): Bitmap =
        Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).apply { eraseColor(color) }

    private fun checkerboard(size: Int, block: Int): Bitmap {
        val pixels = IntArray(size * size)
        for (y in 0 until size) {
            for (x in 0 until size) {
                val on = ((x / block) + (y / block)) % 2 == 0
                pixels[y * size + x] = if (on) Color.WHITE else Color.BLACK
            }
        }
        return Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
            .apply { setPixels(pixels, 0, size, 0, 0, size, size) }
    }

    private fun saveJpeg(bmp: Bitmap, name: String): File =
        File(ctx.cacheDir, name).also { f ->
            FileOutputStream(f).use { bmp.compress(Bitmap.CompressFormat.JPEG, 95, it) }
            bmp.recycle()
        }

    private fun savePng(bmp: Bitmap, name: String): File =
        File(ctx.cacheDir, name).also { f ->
            FileOutputStream(f).use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
            bmp.recycle()
        }

    private fun decodeBounds(file: File): Pair<Int, Int> {
        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, opts)
        return opts.outWidth to opts.outHeight
    }
}
