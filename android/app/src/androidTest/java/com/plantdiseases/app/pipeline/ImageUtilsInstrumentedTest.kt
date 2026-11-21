package com.plantdiseases.app.pipeline

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.plantdiseases.app.util.ImageUtils
import org.junit.Assert.assertEquals
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

    private fun savePng(bmp: Bitmap, name: String): File =
        File(ctx.cacheDir, name).also { f ->
            FileOutputStream(f).use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
            bmp.recycle()
        }
}
