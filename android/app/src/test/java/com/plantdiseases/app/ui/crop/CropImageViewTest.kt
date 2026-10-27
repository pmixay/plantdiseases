package com.plantdiseases.app.ui.crop

import android.content.Context
import android.graphics.Bitmap
import android.view.MotionEvent
import android.view.View
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Robolectric coverage of [CropImageView]'s view↔source-pixel mapping and
 * crop-window state — the deterministic math that must hold for the cropped
 * output to land at full source resolution. Touch gestures and the painted
 * overlay are exercised manually on-device; here we drive the public API
 * (layout → setBitmap → getCroppedBitmap / cropRectNormalized / rotate).
 *
 * Robolectric's default screen is mdpi (density 1.0), so the density-scaled
 * min-crop (56dp = 56px) never clamps the rects used below.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CropImageViewTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private fun laidOutView(w: Int, h: Int): CropImageView {
        val view = CropImageView(context)
        view.measure(
            View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY),
        )
        view.layout(0, 0, w, h)
        return view
    }

    @Test
    fun noBitmap_getCroppedBitmapIsNull() {
        assertThat(laidOutView(300, 300).getCroppedBitmap()).isNull()
    }

    @Test
    fun fullCrop_mapsToFullSourceResolution() {
        val view = laidOutView(300, 300)
        // 120×80 letterboxed into 300×300 → fitScale 2.5, imageRect (0,50,300,250),
        // default crop = full image.
        view.setBitmap(Bitmap.createBitmap(120, 80, Bitmap.Config.ARGB_8888))

        val norm = view.cropRectNormalized()
        assertThat(norm[0]).isWithin(0.001f).of(0f)
        assertThat(norm[1]).isWithin(0.001f).of(0f)
        assertThat(norm[2]).isWithin(0.001f).of(1f)
        assertThat(norm[3]).isWithin(0.001f).of(1f)

        val cropped = view.getCroppedBitmap()!!
        assertThat(cropped.width).isEqualTo(120)
        assertThat(cropped.height).isEqualTo(80)
    }

    @Test
    fun restoreState_appliesNormalizedCropRect() {
        val view = laidOutView(300, 300)
        // Restore a centred half-size window before the bitmap is set.
        view.restoreState(0, floatArrayOf(0.25f, 0.25f, 0.75f, 0.75f))
        view.setBitmap(Bitmap.createBitmap(120, 80, Bitmap.Config.ARGB_8888))

        val norm = view.cropRectNormalized()
        assertThat(norm[0]).isWithin(0.01f).of(0.25f)
        assertThat(norm[2]).isWithin(0.01f).of(0.75f)

        // Half of 120×80 → 60×40 at source resolution.
        val cropped = view.getCroppedBitmap()!!
        assertThat(cropped.width).isEqualTo(60)
        assertThat(cropped.height).isEqualTo(40)
    }

    @Test
    fun rotate90_updatesRotationAndStillCrops() {
        val view = laidOutView(300, 300)
        view.setBitmap(Bitmap.createBitmap(120, 80, Bitmap.Config.ARGB_8888))
        view.rotate90()
        assertThat(view.rotationValue()).isEqualTo(90)
        assertThat(view.getCroppedBitmap()).isNotNull()
    }

    @Test
    fun circularMode_previewsAndExportsTheSameSquareAvatarArea() {
        val view = laidOutView(300, 300)
        view.setCircularCrop(true)
        val source = Bitmap.createBitmap(120, 80, Bitmap.Config.ARGB_8888)
        view.setBitmap(source)

        val normalized = view.cropRectNormalized()
        assertThat(normalized[0]).isWithin(0.01f).of(1f / 6f)
        assertThat(normalized[1]).isWithin(0.01f).of(0f)
        assertThat(normalized[2]).isWithin(0.01f).of(5f / 6f)
        assertThat(normalized[3]).isWithin(0.01f).of(1f)

        val cropped = view.getCroppedBitmap()!!
        assertThat(cropped.width).isEqualTo(80)
        assertThat(cropped.height).isEqualTo(80)
    }

    @Test
    fun circularMode_resizeHandleKeepsTheExportSquare() {
        val view = laidOutView(300, 300)
        view.setCircularCrop(true)
        view.setBitmap(Bitmap.createBitmap(120, 80, Bitmap.Config.ARGB_8888))

        view.onTouchEvent(MotionEvent.obtain(0, 0, MotionEvent.ACTION_DOWN, 250f, 150f, 0))
        view.onTouchEvent(MotionEvent.obtain(0, 16, MotionEvent.ACTION_MOVE, 200f, 150f, 0))
        view.onTouchEvent(MotionEvent.obtain(0, 32, MotionEvent.ACTION_UP, 200f, 150f, 0))

        val cropped = view.getCroppedBitmap()!!
        assertThat(cropped.width).isEqualTo(cropped.height)
        assertThat(cropped.width).isLessThan(80)
    }
}
